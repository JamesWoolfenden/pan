from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from backend.db import (
    find_similar_embeddings,
    get_playlist_track_ids,
    get_tracks_by_ids,
    rank_by_embedding,
    search_embeddings,
    tags_list,
)
from backend.paths import resolve as resolve_file_path
from backend.search.cloud_query_parser import parse_query
from backend.search.embeddings import embed_text
from backend.search.query_router import is_complex_query

VECTOR_CANDIDATE_MULTIPLIER = 8

# Genre-movement terms that don't literally appear in any track's genre tag but should still
# surface it - e.g. no track is tagged "2 Tone", but "two tone"/"2 tone" names the early-80s UK
# ska revival scene (The Specials, Madness, ...), so it should match genre="Ska" tracks. A free
# local lookup, not a cloud_query_parser case, since it's a fixed vocabulary rather than something
# that needs language understanding per query.
GENRE_ALIASES = {
    r"\btwo[\s-]?tone\b": "ska",
    r"\b2[\s-]?tone\b": "ska",
}


def _apply_genre_aliases(query: str, filt: dict) -> dict:
    for pattern, genre in GENRE_ALIASES.items():
        if re.search(pattern, query, re.IGNORECASE):
            genre_include = filt.setdefault("genre_include", [])
            if genre not in genre_include:
                genre_include.append(genre)
    return filt


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
    # tags is stored comma-joined (see db.tags_list) - wrapping both sides in commas and matching
    # ",tag," rather than a plain substring avoids "rock" matching a stored tag like "baroque".
    for tag in filt.get("tag_include") or []:
        clauses.append("(',' || COALESCE(tags, '') || ',') LIKE ?")
        params.append(f"%,{tag},%")
    for tag in filt.get("tag_exclude") or []:
        clauses.append("(',' || COALESCE(tags, '') || ',') NOT LIKE ?")
        params.append(f"%,{tag},%")
    # A plain `year >= ? AND year <= ?` clause always evaluates false for year IS NULL rows,
    # silently dropping genuine matches with missing year metadata (e.g. Ghosttown by The
    # Specials has no year tagged). That's an acceptable risk for a decade-only query, but
    # once genre/mood/tags has already done real narrowing, letting undated tracks through the
    # year clause too catches far more than it lets through wrongly.
    has_content_filter = bool(filt.get("genre_include") or filt.get("mood_include") or filt.get("tag_include"))
    if filt.get("year_min"):
        clause = "year >= ?"
        if has_content_filter:
            clause = f"(year IS NULL OR {clause})"
        clauses.append(clause)
        params.append(filt["year_min"])
    if filt.get("year_max"):
        clause = "year <= ?"
        if has_content_filter:
            clause = f"(year IS NULL OR {clause})"
        clauses.append(clause)
        params.append(filt["year_max"])

    if not clauses:
        return None

    rows = conn.execute(f"SELECT id FROM tracks WHERE {' AND '.join(clauses)}", params).fetchall()
    return {row["id"] for row in rows}


def _normalize_artist(artist: str) -> str:
    """Collapse Plex's reversed-article sort form ("Specials, The") and the natural form
    ("The Specials") to the same key, so the same artist from different sources dedupes
    instead of showing up as two separate "artists" in search results. The key just needs to
    be consistent, not readable, so stripping the article is simpler than reconstructing it."""
    artist = artist.strip().lower()
    if artist.endswith(", the"):
        artist = artist[: -len(", the")]
    elif artist.startswith("the "):
        artist = artist[len("the "):]
    return artist


def _song_key(row: sqlite3.Row) -> tuple[str, str]:
    return (row["title"] or "").strip().lower(), _normalize_artist(row["artist"] or "")


# Lossless > better-lossy > common-lossy > worse-lossy > unknown/unplayable.
FORMAT_QUALITY_TIER = {
    ".flac": 4, ".wav": 4, ".alac": 4,
    ".m4a": 3, ".aac": 3,
    ".mp3": 2, ".ogg": 2,
    ".wma": 1,
}


def _file_quality(row: sqlite3.Row) -> tuple[int, int]:
    path = resolve_file_path(row["file_path"])
    if path is None:
        return (-1, 0)
    tier = FORMAT_QUALITY_TIER.get(path.suffix.lower(), 0)
    try:
        size = path.stat().st_size
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


def search(conn: sqlite3.Connection, query: str, limit: int = 25, offset: int = 0) -> list[dict]:
    filt = parse_query(query) if is_complex_query(query) else {"semantic_query": query}
    filt = _apply_genre_aliases(query, filt)

    candidate_ids = _filtered_candidate_ids(conn, filt)
    semantic_query = filt.get("semantic_query") or query
    query_embedding = embed_text(semantic_query)

    if candidate_ids is not None:
        # A hard filter (genre/year/etc.) narrows the field to a specific set of tracks, which
        # can rank arbitrarily poorly against whatever's left of the query once the filtered
        # part is stripped out (e.g. "1990s house classics" -> filter handles genre+year,
        # leaving just "classic" as the semantic remainder - too weak a signal to guarantee the
        # real matches land in a bounded top-N KNN scan, which sqlite-vec also caps at k=4096
        # regardless). Rank just the filtered candidates directly instead of intersecting with
        # a capped KNN window, so a valid match can never be silently dropped.
        hits = rank_by_embedding(conn, list(candidate_ids), query_embedding)
    else:
        vector_limit = (offset + limit) * VECTOR_CANDIDATE_MULTIPLIER
        hits = search_embeddings(conn, query_embedding, limit=vector_limit)

    ids_in_order = [track_id for track_id, _ in hits]
    distance_by_id = dict(hits)
    rows = get_tracks_by_ids(conn, ids_in_order)
    rows = _dedupe_by_song(rows, offset + limit)[offset:]
    return [_to_result(row, distance_by_id[row["id"]]) for row in rows]


# Pure text-embedding similarity has a real ceiling: it can only approximate numeric audio
# attributes (tempo/energy/key) and recency (year) via fuzzy text tokens, not use them
# precisely. These weights combine the text embedding's distance (genre/mood/artist/album -
# the one thing that's genuinely text) with explicit numeric factors for the data we actually
# have exact values for. Swapping in a real audio-content embedding distance later is a
# drop-in addition to this same weighted sum (one more term + weight), not a redesign.
SIMILARITY_WEIGHTS = {"text": 0.45, "tempo": 0.20, "energy": 0.10, "key": 0.05, "year": 0.20}
TEMPO_NORM = 40.0  # BPM difference treated as "maximally different" for scoring purposes
YEAR_NORM = 15.0  # years apart treated as "maximally different" for scoring purposes


def _numeric_distance(a: float | None, b: float | None, norm: float) -> float:
    """0 = identical, 1 = at-or-beyond `norm` apart. 0.5 (neutral - neither rewarded nor
    penalized) when either value is unknown, rather than guessing."""
    if a is None or b is None:
        return 0.5
    return min(abs(a - b) / norm, 1.0)


def _key_distance(a: str | None, b: str | None) -> float:
    if a is None or b is None:
        return 0.5
    return 0.0 if a == b else 1.0


def _combined_distance(anchor: sqlite3.Row, candidate: sqlite3.Row, text_distance: float) -> float:
    w = SIMILARITY_WEIGHTS
    return (
        w["text"] * text_distance
        + w["tempo"] * _numeric_distance(anchor["tempo"], candidate["tempo"], TEMPO_NORM)
        + w["energy"] * _numeric_distance(anchor["energy"], candidate["energy"], 1.0)
        + w["key"] * _key_distance(anchor["musical_key"], candidate["musical_key"])
        + w["year"] * _numeric_distance(anchor["year"], candidate["year"], YEAR_NORM)
    )


def similar(conn: sqlite3.Connection, track_id: int, limit: int = 25) -> list[dict]:
    """Find tracks most like the given track's own - "more like this" - combining the text
    embedding's distance with explicit tempo/energy/key/year factors (see _combined_distance)
    rather than relying on the text embedding alone."""
    anchor_rows = get_tracks_by_ids(conn, [track_id])
    if not anchor_rows:
        return []
    anchor = anchor_rows[0]
    anchor_key = _song_key(anchor)

    hits = find_similar_embeddings(conn, track_id, limit=(limit + 1) * VECTOR_CANDIDATE_MULTIPLIER)
    hits = [(tid, dist) for tid, dist in hits if tid != track_id]

    ids_in_order = [tid for tid, _ in hits]
    text_distance_by_id = dict(hits)
    rows = get_tracks_by_ids(conn, ids_in_order)
    # Exclude other physical copies of the anchor track itself, not just its own id - "similar
    # to X" shouldn't recommend X back to you under a different filename.
    rows = [row for row in rows if _song_key(row) != anchor_key]

    combined_distance_by_id = {
        row["id"]: _combined_distance(anchor, row, text_distance_by_id[row["id"]]) for row in rows
    }
    rows.sort(key=lambda row: combined_distance_by_id[row["id"]])

    rows = _dedupe_by_song(rows, limit)
    return [_to_result(row, combined_distance_by_id[row["id"]]) for row in rows]


_TRACK_NUMBER_RE = re.compile(r"^\s*(\d+)")


def _track_number(row: sqlite3.Row) -> int | None:
    if not row["file_path"]:
        return None
    match = _TRACK_NUMBER_RE.match(Path(row["file_path"]).stem)
    return int(match.group(1)) if match else None


def album_tracks(conn: sqlite3.Connection, track_id: int, limit: int = 200) -> list[dict]:
    """All tracks from the same album as the given track, in track order."""
    anchor_rows = get_tracks_by_ids(conn, [track_id])
    if not anchor_rows or not anchor_rows[0]["album"]:
        return []
    anchor = anchor_rows[0]
    return album_tracks_by_name(conn, anchor["artist"], anchor["album"], limit)


def album_tracks_by_name(conn: sqlite3.Connection, artist: str, album: str, limit: int = 200) -> list[dict]:
    """All tracks for a given (artist, album), in track order (parsed from the leading number
    in each file's name, since the catalog has no explicit track-number field), deduped the
    same way search results are."""
    rows = conn.execute(
        "SELECT * FROM tracks WHERE lower(trim(artist)) = ? AND lower(trim(album)) = ?",
        ((artist or "").strip().lower(), (album or "").strip().lower()),
    ).fetchall()

    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault(_song_key(row), []).append(row)
    deduped = [members[0] if len(members) == 1 else max(members, key=_file_quality) for members in groups.values()]
    deduped.sort(key=lambda r: (_track_number(r) is None, _track_number(r) or 0, (r["title"] or "").lower()))

    return [_to_result(row, 0.0) for row in deduped[:limit]]


def discography(conn: sqlite3.Connection, artist: str) -> list[dict]:
    """Every album by this artist, grouped with year and (deduped) track count, oldest first."""
    artist_norm = (artist or "").strip().lower()
    if not artist_norm:
        return []

    rows = conn.execute("SELECT * FROM tracks WHERE lower(trim(artist)) = ?", (artist_norm,)).fetchall()

    albums: dict[str, dict] = {}
    for row in rows:
        album = (row["album"] or "").strip()
        if not album:
            continue
        entry = albums.setdefault(album.lower(), {"album": album, "year": row["year"], "songs": set()})
        entry["songs"].add(_song_key(row))
        if row["year"] and not entry["year"]:
            entry["year"] = row["year"]

    result = [
        {"album": a["album"], "year": a["year"], "track_count": len(a["songs"])}
        for a in albums.values()
    ]
    result.sort(key=lambda a: (a["year"] is None, a["year"] or 0, a["album"].lower()))
    return result


def playlist_tracks(conn: sqlite3.Connection, playlist_id: int) -> list[dict]:
    """A saved playlist's tracks, in the order they were saved."""
    ids = get_playlist_track_ids(conn, playlist_id)
    rows = get_tracks_by_ids(conn, ids)
    return [_to_result(row, 0.0) for row in rows]


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
        "tags": tags_list(row),
        "year": row["year"],
        "source": row["source"],
        "file_path": row["file_path"],
        "plex_rating_key": row["plex_rating_key"],
        "favorite": bool(row["favorited_at"]),
        "score": 1 - distance,
    }
