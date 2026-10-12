from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from backend.db import find_similar_embeddings, get_tracks_by_ids, search_embeddings
from backend.search.cloud_query_parser import parse_query
from backend.search.embeddings import embed_text
from backend.search.query_router import is_complex_query

VECTOR_CANDIDATE_MULTIPLIER = 8


def _filtered_candidate_ids(conn: sqlite3.Connection, filt: dict) -> set[int] | None:
    """Build the set of track ids matching the structured filter's hard constraints.
    Returns None if the filter has no hard constraints (meaning: don't restrict)."""
    clauses: list[str] = []
    params: list = []

    for genre in filt.get("genre_include") or []:
        clauses.append("genre LIKE ?")
        params.append(f"%{genre}%")
    for mood in filt.get("mood_include") or []:
        clauses.append("mood LIKE ?")
        params.append(f"%{mood}%")
    for genre in filt.get("genre_exclude") or []:
        clauses.append("(genre IS NULL OR genre NOT LIKE ?)")
        params.append(f"%{genre}%")
    for mood in filt.get("mood_exclude") or []:
        clauses.append("(mood IS NULL OR mood NOT LIKE ?)")
        params.append(f"%{mood}%")
    for artist in filt.get("artist_exclude") or []:
        clauses.append("(artist IS NULL OR artist NOT LIKE ?)")
        params.append(f"%{artist}%")
    if filt.get("year_min"):
        clauses.append("year >= ?")
        params.append(filt["year_min"])
    if filt.get("year_max"):
        clauses.append("year <= ?")
        params.append(filt["year_max"])

    if not clauses:
        return None

    rows = conn.execute(f"SELECT id FROM tracks WHERE {' AND '.join(clauses)}", params).fetchall()
    return {row["id"] for row in rows}


def _song_key(row: sqlite3.Row) -> tuple[str, str]:
    return (row["title"] or "").strip().lower(), (row["artist"] or "").strip().lower()


# Lossless > better-lossy > common-lossy > worse-lossy > unknown/unplayable.
FORMAT_QUALITY_TIER = {
    ".flac": 4, ".wav": 4, ".alac": 4,
    ".m4a": 3, ".aac": 3,
    ".mp3": 2, ".ogg": 2,
    ".wma": 1,
}


def _file_quality(row: sqlite3.Row) -> tuple[int, int]:
    path = row["file_path"]
    if not path:
        return (-1, 0)
    tier = FORMAT_QUALITY_TIER.get(Path(path).suffix.lower(), 0)
    try:
        size = Path(path).stat().st_size
    except OSError:
        size = 0
    return (tier, size)


def _dedupe_by_song(rows: list[sqlite3.Row], limit: int) -> list[sqlite3.Row]:
    """A library can have the same song as several separate physical files (different rips,
    duplicate imports, etc.) - each a distinct catalog row, sometimes with conflicting tags and
    differing audio quality. Collapse each (title, artist) group to a single result, ranked in
    the list by the group's best match but represented by its highest-quality file (lossless
    over lossy, then larger file size as a bitrate proxy) - preferring the file you'd actually
    want to listen to, not just whichever happened to embed closest to the query."""
    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    first_rank: dict[tuple[str, str], int] = {}
    for rank, row in enumerate(rows):
        key = _song_key(row)
        groups.setdefault(key, []).append(row)
        first_rank.setdefault(key, rank)

    ordered_keys = sorted(groups, key=lambda k: first_rank[k])[:limit]
    return [
        members[0] if len(members) == 1 else max(members, key=_file_quality)
        for members in (groups[key] for key in ordered_keys)
    ]


def search(conn: sqlite3.Connection, query: str, limit: int = 25) -> list[dict]:
    filt = parse_query(query) if is_complex_query(query) else {"semantic_query": query}

    candidate_ids = _filtered_candidate_ids(conn, filt)
    semantic_query = filt.get("semantic_query") or query

    vector_limit = limit * VECTOR_CANDIDATE_MULTIPLIER
    query_embedding = embed_text(semantic_query)
    hits = search_embeddings(conn, query_embedding, limit=vector_limit)

    if candidate_ids is not None:
        hits = [(track_id, dist) for track_id, dist in hits if track_id in candidate_ids]

    ids_in_order = [track_id for track_id, _ in hits]
    distance_by_id = dict(hits)
    rows = get_tracks_by_ids(conn, ids_in_order)
    rows = _dedupe_by_song(rows, limit)
    return [_to_result(row, distance_by_id[row["id"]]) for row in rows]


def similar(conn: sqlite3.Connection, track_id: int, limit: int = 25) -> list[dict]:
    """Find tracks whose embedding is closest to the given track's own - "more like this",
    using the vector already stored for it rather than re-embedding anything."""
    anchor_rows = get_tracks_by_ids(conn, [track_id])
    anchor_key = _song_key(anchor_rows[0]) if anchor_rows else None

    hits = find_similar_embeddings(conn, track_id, limit=(limit + 1) * VECTOR_CANDIDATE_MULTIPLIER)
    hits = [(tid, dist) for tid, dist in hits if tid != track_id]

    ids_in_order = [tid for tid, _ in hits]
    distance_by_id = dict(hits)
    rows = get_tracks_by_ids(conn, ids_in_order)
    # Exclude other physical copies of the anchor track itself, not just its own id - "similar
    # to X" shouldn't recommend X back to you under a different filename.
    rows = [row for row in rows if _song_key(row) != anchor_key]
    rows = _dedupe_by_song(rows, limit)
    return [_to_result(row, distance_by_id[row["id"]]) for row in rows]


_TRACK_NUMBER_RE = re.compile(r"^\s*(\d+)")


def _track_number(row: sqlite3.Row) -> int | None:
    if not row["file_path"]:
        return None
    match = _TRACK_NUMBER_RE.match(Path(row["file_path"]).stem)
    return int(match.group(1)) if match else None


def album_tracks(conn: sqlite3.Connection, track_id: int, limit: int = 200) -> list[dict]:
    """All tracks from the same album as the given track, in track order (parsed from the
    leading number in each file's name, since the catalog has no explicit track-number field),
    deduped the same way search results are."""
    anchor_rows = get_tracks_by_ids(conn, [track_id])
    if not anchor_rows or not anchor_rows[0]["album"]:
        return []
    anchor = anchor_rows[0]

    rows = conn.execute(
        "SELECT * FROM tracks WHERE lower(trim(artist)) = ? AND lower(trim(album)) = ?",
        ((anchor["artist"] or "").strip().lower(), (anchor["album"] or "").strip().lower()),
    ).fetchall()

    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault(_song_key(row), []).append(row)
    deduped = [members[0] if len(members) == 1 else max(members, key=_file_quality) for members in groups.values()]
    deduped.sort(key=lambda r: (_track_number(r) is None, _track_number(r) or 0, (r["title"] or "").lower()))

    return [_to_result(row, 0.0) for row in deduped[:limit]]


_BRACKETED_RE = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_DASH_SUFFIX_RE = re.compile(r"\s+-\s+.+$")


def _base_title(title: str | None) -> str:
    """Strip "(Radio Edit)"/"[Club Mix]"/"- Remix" style suffixes to get the underlying song
    name, so different mixes of the same track can be grouped together."""
    if not title:
        return ""
    stripped = _DASH_SUFFIX_RE.sub("", _BRACKETED_RE.sub("", title))
    return stripped.strip().lower()


def versions(conn: sqlite3.Connection, track_id: int, limit: int = 50) -> list[dict]:
    """Other mixes/edits/remixes of the same song by the same artist - distinct from
    _dedupe_by_song, which collapses exact-title duplicates: here the titles genuinely differ
    ("Sexy Boy [Radio Edit]" vs "Sexy Boy [Cassius Radio Mix]") but share an underlying song."""
    anchor_rows = get_tracks_by_ids(conn, [track_id])
    if not anchor_rows:
        return []
    anchor = anchor_rows[0]
    base = _base_title(anchor["title"])
    artist_norm = (anchor["artist"] or "").strip().lower()
    if not base or not artist_norm:
        return []

    rows = conn.execute(
        "SELECT * FROM tracks WHERE lower(trim(artist)) = ?", (artist_norm,)
    ).fetchall()
    matching = [row for row in rows if _base_title(row["title"]) == base]

    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in matching:
        groups.setdefault(_song_key(row), []).append(row)
    deduped = [members[0] if len(members) == 1 else max(members, key=_file_quality) for members in groups.values()]
    deduped.sort(key=lambda r: (r["title"] or "").lower())

    return [_to_result(row, 0.0) for row in deduped[:limit]]


def _to_result(row: sqlite3.Row, distance: float) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "artist": row["artist"],
        "album": row["album"],
        "genre": row["genre"],
        "mood": row["mood"],
        "year": row["year"],
        "source": row["source"],
        "file_path": row["file_path"],
        "plex_rating_key": row["plex_rating_key"],
        "score": 1 - distance,
    }
