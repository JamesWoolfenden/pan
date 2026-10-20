from __future__ import annotations

import sqlite3
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import sqlite_vec

from backend.config import settings

EMBEDDING_DIM = 384

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,              -- 'plex' or 'local'
    plex_rating_key TEXT,
    file_path TEXT,
    title TEXT,
    artist TEXT,
    album TEXT,
    genre TEXT,
    mood TEXT,
    year INTEGER,
    duration_ms INTEGER,
    tempo REAL,
    energy REAL,
    musical_key TEXT,
    tag_source TEXT NOT NULL DEFAULT 'none',  -- 'none' | 'metadata' | 'local' | 'cloud' | 'manual'
    ai_background TEXT,  -- cached AI-generated "tell me more" writeup, so it's only generated once
    lyrics TEXT,  -- cached lyrics lookup: NULL = not yet looked up, '' = looked up, none found
    synced_lyrics TEXT,  -- time-stamped LRC text alongside `lyrics`, when lrclib has it - NULL
                         -- whenever `lyrics` is NULL/'' too, no separate "looked up" state needed
    tags TEXT,  -- comma-separated freeform tags, manually added - separate from genre/mood, which
                -- are single-value fields filled by the local/cloud tagger
    youtube_video_id TEXT,  -- cached YouTube match (backend/youtube.py): NULL = not yet looked
                            -- up, '' = looked up, the text search itself found nothing - same NULL/''
                            -- convention as lyrics, so it's only ever looked up once
    youtube_candidates TEXT,  -- JSON array of runner-up video ids from that same lookup, ranked
                              -- best-first, excluding youtube_video_id itself. Lets a dismiss
                              -- (the current pick's actual embed turned out broken - common, since
                              -- the YouTube API's "embeddable" flag isn't fully reliable) try the
                              -- next-best candidate instead of just giving up, with no extra
                              -- search.list call (quota is scarce: 100/day on the free tier)
    youtube_watch_id TEXT,  -- the original best match from the initial lookup, set once and never
                            -- touched by a dismiss (unlike youtube_video_id, which advances/empties
                            -- as video mode works through youtube_candidates). Some tracks have
                            -- every embeddable candidate blocked by a rights-holder's Content ID
                            -- policy (undetectable in advance - see backend/youtube.py) - this
                            -- still gives the Info panel's "Video" button a direct link to the
                            -- right video on youtube.com even once video mode has given up on it
    favorited_at TEXT,  -- NULL = not a favorite; a timestamp (not just a boolean) so "Favorites"
                       -- can be listed most-recently-favorited first
    disliked_at TEXT,  -- the opposite of favorited_at, same reasoning - mutually exclusive with
                       -- it (setting one clears the other), enforced in set_favorite/set_dislike
    identity_source TEXT NOT NULL DEFAULT 'auto'  -- 'auto' | 'manual' - 'manual' once title/artist/
                       -- album has been corrected by hand (or a migration script), so a later
                       -- Plex/local rescan doesn't silently revert it back to the source's own
                       -- (wrong) metadata. See upsert_track/merge_plex_metadata.
);
CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);
CREATE INDEX IF NOT EXISTS idx_tracks_genre ON tracks(genre);

CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    hidden_at TEXT  -- set to hide a playlist from the default list (e.g. one-off/rarely-used
                     -- playlists cluttering the panel) without deleting it - see list_playlists
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id INTEGER NOT NULL,
    track_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, position)
);
CREATE INDEX IF NOT EXISTS idx_playlist_tracks_playlist ON playlist_tracks(playlist_id);

-- An event log (not just a counter) so later analysis - skip patterns, taste over time, etc. -
-- has raw data to work with, not just an aggregate. percent_listened lets "never played"
-- (a discoverability problem - might be a hidden gem) be told apart from "played and skipped
-- immediately" (a genuine dislike signal), rather than treating both as equally "bad".
CREATE TABLE IF NOT EXISTS play_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER NOT NULL,
    played_at TEXT NOT NULL DEFAULT (datetime('now')),
    percent_listened REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_play_events_track ON play_events(track_id);

-- Single-row (id=1) table, not per-device: the point is one shared "what was I listening to"
-- state that follows you between devices, not independent state per device. Whichever device
-- last wrote wins; a device picks it up on load if it's newer than what it has locally.
CREATE TABLE IF NOT EXISTS now_playing (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Player event log (ui/app.js's logClient -> POST /client-log), for debugging playback issues
-- that only show up on a device there's no devtools access to (e.g. a locked phone). Pruned to
-- the most recent rows on insert so this can't grow unbounded.
CREATE TABLE IF NOT EXISTS client_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    event TEXT NOT NULL,
    detail TEXT
);

-- Library-improvement suggestions (backend/recommend.py), refreshed as the last step of a
-- reindex rather than on a separate schedule - "regularly" means "whenever you reindex", no
-- extra scheduling needed. Append-only (run_id groups one refresh's rows) rather than wholesale
-- replaced, so past runs stay queryable - that history is what keeps later runs from suggesting
-- the same artists over and over (see backend/recommend.py's seed rotation/exclusion logic).
-- The UI only ever shows the latest run_id's rows.
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    category TEXT NOT NULL,  -- 'gap_fill' | 'discovery'
    artist TEXT NOT NULL,
    reasoning TEXT,
    detail TEXT,  -- JSON, e.g. {"missing_albums": [...]} for gap_fill
    status TEXT NOT NULL DEFAULT 'pending'  -- 'pending' | 'accepted' | 'dismissed'
);

-- Startup/shutdown/reindex-start events, so a gap in uptime (e.g. the NAS deployment going
-- unreachable) can be diagnosed - was the previous process stopped cleanly or killed, and was a
-- reindex running at the time - without cross-referencing the container's raw stdout/stderr log
-- in Container Manager.
CREATE TABLE IF NOT EXISTS app_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,  -- 'startup' | 'shutdown' | 'reindex_start'
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CLIENT_LOGS_MAX_ROWS = 2000


@dataclass
class Track:
    external_id: str
    source: str
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str | None = None
    mood: str | None = None
    year: int | None = None
    duration_ms: int | None = None
    tempo: float | None = None
    energy: float | None = None
    musical_key: str | None = None
    plex_rating_key: str | None = None
    file_path: str | None = None
    tag_source: str = "none"
    id: int | None = field(default=None)


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or settings.db_file), timeout=180)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 180000")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


@contextmanager
def connection(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS track_vectors USING vec0(embedding FLOAT[{EMBEDDING_DIM}])"
    )
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS doesn't add new columns to an already-existing tracks table
    (e.g. the real library's pan.db from before ai_background existed) - add any that are
    missing so older databases pick up new columns without a full reindex."""
    existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()
    }
    if "ai_background" not in existing_columns:
        conn.execute("ALTER TABLE tracks ADD COLUMN ai_background TEXT")
    if "tags" not in existing_columns:
        conn.execute("ALTER TABLE tracks ADD COLUMN tags TEXT")
    if "favorited_at" not in existing_columns:
        conn.execute("ALTER TABLE tracks ADD COLUMN favorited_at TEXT")
    if "lyrics" not in existing_columns:
        conn.execute("ALTER TABLE tracks ADD COLUMN lyrics TEXT")
    if "synced_lyrics" not in existing_columns:
        conn.execute("ALTER TABLE tracks ADD COLUMN synced_lyrics TEXT")
    if "identity_source" not in existing_columns:
        conn.execute(
            "ALTER TABLE tracks ADD COLUMN identity_source TEXT NOT NULL DEFAULT 'auto'"
        )
    if "youtube_video_id" not in existing_columns:
        conn.execute("ALTER TABLE tracks ADD COLUMN youtube_video_id TEXT")
    if "youtube_candidates" not in existing_columns:
        conn.execute("ALTER TABLE tracks ADD COLUMN youtube_candidates TEXT")
    if "youtube_watch_id" not in existing_columns:
        conn.execute("ALTER TABLE tracks ADD COLUMN youtube_watch_id TEXT")
    if "disliked_at" not in existing_columns:
        conn.execute("ALTER TABLE tracks ADD COLUMN disliked_at TEXT")

    playlists_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(playlists)").fetchall()
    }
    if playlists_columns and "hidden_at" not in playlists_columns:
        conn.execute("ALTER TABLE playlists ADD COLUMN hidden_at TEXT")

    recommendations_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(recommendations)").fetchall()
    }
    if recommendations_columns and "run_id" not in recommendations_columns:
        conn.execute(
            "ALTER TABLE recommendations ADD COLUMN run_id INTEGER NOT NULL DEFAULT 0"
        )
    if recommendations_columns and "status" not in recommendations_columns:
        conn.execute(
            "ALTER TABLE recommendations ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
        )


def upsert_track(conn: sqlite3.Connection, track: Track) -> int:
    existing = conn.execute(
        "SELECT id, tag_source FROM tracks WHERE external_id = ?", (track.external_id,)
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE tracks SET source=?, plex_rating_key=?, file_path=?,
                title=CASE WHEN identity_source='manual' THEN title ELSE ? END,
                artist=CASE WHEN identity_source='manual' THEN artist ELSE ? END,
                album=CASE WHEN identity_source='manual' THEN album ELSE ? END,
                genre=COALESCE(?, genre), mood=COALESCE(?, mood), year=?,
                duration_ms=?, tempo=COALESCE(?, tempo), energy=COALESCE(?, energy),
                musical_key=COALESCE(?, musical_key)
            WHERE id=?
            """,
            (
                track.source,
                track.plex_rating_key,
                track.file_path,
                track.title,
                track.artist,
                track.album,
                track.genre,
                track.mood,
                track.year,
                track.duration_ms,
                track.tempo,
                track.energy,
                track.musical_key,
                existing["id"],
            ),
        )
        return existing["id"]

    cur = conn.execute(
        """
        INSERT INTO tracks (
            external_id, source, plex_rating_key, file_path, title, artist, album,
            genre, mood, year, duration_ms, tempo, energy, musical_key, tag_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            track.external_id,
            track.source,
            track.plex_rating_key,
            track.file_path,
            track.title,
            track.artist,
            track.album,
            track.genre,
            track.mood,
            track.year,
            track.duration_ms,
            track.tempo,
            track.energy,
            track.musical_key,
            track.tag_source,
        ),
    )
    return cur.lastrowid


def set_tags(
    conn: sqlite3.Connection,
    track_id: int,
    genre: str | None,
    mood: str | None,
    tag_source: str,
) -> None:
    conn.execute(
        "UPDATE tracks SET genre=?, mood=?, tag_source=? WHERE id=?",
        (genre, mood, tag_source, track_id),
    )


def tags_list(row: sqlite3.Row) -> list[str]:
    """Freeform tags are stored as a single comma-separated column rather than a join table -
    the library's not big enough to need relational tag queries, and this keeps storage/editing
    as simple as genre/mood already are. This is the one place that format is parsed."""
    raw = row["tags"]
    return [t for t in (raw or "").split(",") if t]


def set_tags_list(conn: sqlite3.Connection, track_id: int, tags: list[str]) -> None:
    normalized = sorted({t.strip().lower() for t in tags if t.strip()})
    conn.execute(
        "UPDATE tracks SET tags=? WHERE id=?", (",".join(normalized) or None, track_id)
    )


def set_artist(conn: sqlite3.Connection, track_id: int, artist: str) -> None:
    # identity_source='manual' protects this from being silently reverted by a later Plex/local
    # rescan - see upsert_track/merge_plex_metadata.
    conn.execute(
        "UPDATE tracks SET artist=?, identity_source='manual' WHERE id=?",
        (artist, track_id),
    )


def set_title(conn: sqlite3.Connection, track_id: int, title: str) -> None:
    conn.execute(
        "UPDATE tracks SET title=?, identity_source='manual' WHERE id=?",
        (title, track_id),
    )


def set_album(conn: sqlite3.Connection, track_id: int, album: str) -> None:
    conn.execute(
        "UPDATE tracks SET album=?, identity_source='manual' WHERE id=?",
        (album, track_id),
    )


def set_favorite(conn: sqlite3.Connection, track_id: int, favorite: bool) -> None:
    # Mutually exclusive with dislike: liking a previously-disliked track clears the dislike,
    # same as un-liking just clears favorited_at and leaves any dislike alone.
    conn.execute(
        "UPDATE tracks SET favorited_at = ?, disliked_at = CASE WHEN ? THEN NULL ELSE disliked_at END WHERE id = ?",
        (
            datetime.now(UTC).isoformat() if favorite else None,
            favorite,
            track_id,
        ),
    )


def set_dislike(conn: sqlite3.Connection, track_id: int, dislike: bool) -> None:
    conn.execute(
        "UPDATE tracks SET disliked_at = ?, favorited_at = CASE WHEN ? THEN NULL ELSE favorited_at END WHERE id = ?",
        (
            datetime.now(UTC).isoformat() if dislike else None,
            dislike,
            track_id,
        ),
    )


def set_audio_features(
    conn: sqlite3.Connection,
    track_id: int,
    tempo: float,
    energy: float,
    musical_key: str,
) -> None:
    conn.execute(
        "UPDATE tracks SET tempo=?, energy=?, musical_key=? WHERE id=?",
        (tempo, energy, musical_key, track_id),
    )


def set_ai_background(conn: sqlite3.Connection, track_id: int, text: str) -> None:
    conn.execute("UPDATE tracks SET ai_background=? WHERE id=?", (text, track_id))


def set_year(conn: sqlite3.Connection, track_id: int, year: int) -> None:
    conn.execute("UPDATE tracks SET year=? WHERE id=?", (year, track_id))


def set_youtube_video_id(
    conn: sqlite3.Connection,
    track_id: int,
    video_id: str,
    candidates_json: str = "[]",
    watch_id: str | None = None,
) -> None:
    """watch_id is only passed on a fresh lookup (not a dismiss's advance through
    youtube_candidates) - see youtube_watch_id's own comment in SCHEMA for why it's a separate,
    never-overwritten field once set."""
    if watch_id is not None:
        conn.execute(
            "UPDATE tracks SET youtube_video_id=?, youtube_candidates=?, youtube_watch_id=? "
            "WHERE id=?",
            (video_id, candidates_json, watch_id, track_id),
        )
    else:
        conn.execute(
            "UPDATE tracks SET youtube_video_id=?, youtube_candidates=? WHERE id=?",
            (video_id, candidates_json, track_id),
        )


def set_lyrics(
    conn: sqlite3.Connection, track_id: int, text: str, synced: str | None = None
) -> None:
    conn.execute(
        "UPDATE tracks SET lyrics=?, synced_lyrics=? WHERE id=?",
        (text, synced, track_id),
    )


def set_file_path(conn: sqlite3.Connection, track_id: int, file_path: str) -> None:
    conn.execute("UPDATE tracks SET file_path=? WHERE id=?", (file_path, track_id))


def merge_plex_metadata(conn: sqlite3.Connection, track_id: int, track: Track) -> None:
    """Enrich an already-cataloged track (e.g. found by the local scan) with Plex's metadata,
    without touching its id - so audio features/embeddings already computed for it survive.
    Leaves title/artist/album alone once identity_source is 'manual' - otherwise Plex's own
    (possibly wrong) metadata for that file silently reverts a deliberate correction on the
    very next ingest, same as upsert_track."""
    conn.execute(
        """
        UPDATE tracks SET
            plex_rating_key = ?,
            source = 'plex',
            title = CASE WHEN identity_source='manual' THEN title ELSE COALESCE(NULLIF(?, ''), title) END,
            artist = CASE WHEN identity_source='manual' THEN artist ELSE COALESCE(NULLIF(?, ''), artist) END,
            album = CASE WHEN identity_source='manual' THEN album ELSE COALESCE(NULLIF(?, ''), album) END,
            genre = COALESCE(?, genre),
            mood = COALESCE(?, mood),
            year = COALESCE(?, year)
        WHERE id = ?
        """,
        (
            track.plex_rating_key,
            track.title,
            track.artist,
            track.album,
            track.genre,
            track.mood,
            track.year,
            track_id,
        ),
    )


def tracks_missing_tags(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM tracks WHERE (genre IS NULL OR mood IS NULL) AND tag_source != 'cloud'"
    ).fetchall()


def tracks_missing_identity(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Tracks with no usable title or artist - e.g. a filename the scanner/tagger couldn't parse
    cleanly. These are otherwise invisible in the app: semantic search has no title/artist text
    to match against, so there's no way to find one to fix it short of querying the database
    directly."""
    return conn.execute(
        "SELECT * FROM tracks WHERE title IS NULL OR title = '' OR artist IS NULL OR artist = ''"
    ).fetchall()


def all_tracks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tracks").fetchall()


def get_track(conn: sqlite3.Connection, track_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()


def get_tracks_by_ids(conn: sqlite3.Connection, ids: list[int]) -> list[sqlite3.Row]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM tracks WHERE id IN ({placeholders})", ids
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    return [by_id[i] for i in ids if i in by_id]


def get_embeddings(
    conn: sqlite3.Connection, track_ids: list[int]
) -> dict[int, list[float]]:
    """Raw stored vectors for a specific set of tracks - used to build an aggregate (e.g. a
    playlist's mean embedding), as opposed to search_embeddings/rank_by_embedding which compare
    *against* a vector rather than returning the vectors themselves. sqlite_vec's Python package
    doesn't expose a deserializer, so unpack the stored float32 blob by hand."""
    if not track_ids:
        return {}
    placeholders = ",".join("?" for _ in track_ids)
    rows = conn.execute(
        f"SELECT rowid, embedding FROM track_vectors WHERE rowid IN ({placeholders})",
        track_ids,
    ).fetchall()
    result = {}
    for row in rows:
        blob = row["embedding"]
        count = len(blob) // 4
        result[row["rowid"]] = list(struct.unpack(f"<{count}f", blob))
    return result


def upsert_embedding(
    conn: sqlite3.Connection, track_id: int, embedding: list[float]
) -> None:
    conn.execute("DELETE FROM track_vectors WHERE rowid = ?", (track_id,))
    conn.execute(
        "INSERT INTO track_vectors (rowid, embedding) VALUES (?, ?)",
        (track_id, sqlite_vec.serialize_float32(embedding)),
    )


def search_embeddings(
    conn: sqlite3.Connection, query_embedding: list[float], limit: int = 25
) -> list[tuple[int, float]]:
    rows = conn.execute(
        """
        SELECT rowid, distance FROM track_vectors
        WHERE embedding MATCH ?
        ORDER BY distance
        LIMIT ?
        """,
        (sqlite_vec.serialize_float32(query_embedding), limit),
    ).fetchall()
    return [(row["rowid"], row["distance"]) for row in rows]


def rank_by_embedding(
    conn: sqlite3.Connection, track_ids: list[int], query_embedding: list[float]
) -> list[tuple[int, float]]:
    """Rank a specific, already-known set of tracks by cosine distance to a query embedding -
    using the vec_distance_cosine scalar function directly rather than vec0's MATCH/KNN query,
    which sqlite-vec caps at k=4096. Needed when a hard filter (genre/year/etc.) has already
    narrowed the field: those candidates can rank arbitrarily poorly (even outside the top
    4096 of 19k+ tracks) against whatever's left of the semantic query, so a KNN scan - capped
    or not - can silently miss genuinely matching tracks entirely."""
    if not track_ids:
        return []
    placeholders = ",".join("?" for _ in track_ids)
    rows = conn.execute(
        f"""
        SELECT rowid, vec_distance_cosine(embedding, ?) AS distance
        FROM track_vectors
        WHERE rowid IN ({placeholders})
        ORDER BY distance
        """,
        (sqlite_vec.serialize_float32(query_embedding), *track_ids),
    ).fetchall()
    return [(row["rowid"], row["distance"]) for row in rows]


def find_similar_embeddings(
    conn: sqlite3.Connection, track_id: int, limit: int = 25
) -> list[tuple[int, float]]:
    """KNN search using a track's own stored embedding as the query vector - entirely in SQL,
    no need to pull the vector into Python and re-serialize it."""
    rows = conn.execute(
        """
        SELECT rowid, distance FROM track_vectors
        WHERE embedding MATCH (SELECT embedding FROM track_vectors WHERE rowid = ?)
        ORDER BY distance
        LIMIT ?
        """,
        (track_id, limit),
    ).fetchall()
    return [(row["rowid"], row["distance"]) for row in rows]


def create_playlist(conn: sqlite3.Connection, name: str, track_ids: list[int]) -> int:
    cur = conn.execute("INSERT INTO playlists (name) VALUES (?)", (name,))
    playlist_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
        [(playlist_id, track_id, i) for i, track_id in enumerate(track_ids)],
    )
    return playlist_id


def set_playlist_tracks(
    conn: sqlite3.Connection, playlist_id: int, track_ids: list[int]
) -> None:
    """Replace a playlist's full track list (order included) - used when a queue that was loaded
    from this playlist gets edited and saved back, unlike add_track_to_playlist's single-track
    append."""
    conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
    conn.executemany(
        "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
        [(playlist_id, track_id, i) for i, track_id in enumerate(track_ids)],
    )


def add_track_to_playlist(
    conn: sqlite3.Connection, playlist_id: int, track_id: int
) -> None:
    # One statement, not a separate SELECT MAX(position) followed by an INSERT - two concurrent
    # adds to the same playlist (e.g. clicking "+ Playlist" on two tracks in quick succession)
    # could otherwise both read the same max position before either commits, then both try to
    # insert it, tripping the (playlist_id, position) unique constraint with a 500. The subquery
    # runs inside the INSERT's own implicit write transaction, so the read and write are atomic
    # against another connection doing the same thing.
    conn.execute(
        """
        INSERT INTO playlist_tracks (playlist_id, track_id, position)
        SELECT ?, ?, COALESCE(MAX(position) + 1, 0) FROM playlist_tracks WHERE playlist_id = ?
        """,
        (playlist_id, track_id, playlist_id),
    )


def list_playlists(
    conn: sqlite3.Connection, include_hidden: bool = False
) -> list[sqlite3.Row]:
    where = "" if include_hidden else "WHERE p.hidden_at IS NULL"
    return conn.execute(
        f"""
        SELECT p.id, p.name, p.created_at, p.hidden_at, COUNT(pt.track_id) AS track_count
        FROM playlists p
        LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
        {where}
        GROUP BY p.id
        ORDER BY p.created_at DESC
        """
    ).fetchall()


def set_playlist_hidden(
    conn: sqlite3.Connection, playlist_id: int, hidden: bool
) -> None:
    conn.execute(
        "UPDATE playlists SET hidden_at = ? WHERE id = ?",
        (datetime.now(UTC).isoformat() if hidden else None, playlist_id),
    )


def get_playlist(conn: sqlite3.Connection, playlist_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM playlists WHERE id = ?", (playlist_id,)
    ).fetchone()


def get_playlist_track_ids(conn: sqlite3.Connection, playlist_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
        (playlist_id,),
    ).fetchall()
    return [row["track_id"] for row in rows]


def rename_playlist(conn: sqlite3.Connection, playlist_id: int, name: str) -> None:
    conn.execute("UPDATE playlists SET name = ? WHERE id = ?", (name, playlist_id))


def delete_playlist(conn: sqlite3.Connection, playlist_id: int) -> None:
    conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
    conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))


def record_play_event(
    conn: sqlite3.Connection, track_id: int, percent_listened: float
) -> None:
    percent_listened = max(0.0, min(1.0, percent_listened))
    conn.execute(
        "INSERT INTO play_events (track_id, percent_listened) VALUES (?, ?)",
        (track_id, percent_listened),
    )


def get_now_playing(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT state, updated_at FROM now_playing WHERE id = 1"
    ).fetchone()


def set_now_playing(conn: sqlite3.Connection, state_json: str) -> None:
    conn.execute(
        """
        INSERT INTO now_playing (id, state, updated_at) VALUES (1, ?, datetime('now'))
        ON CONFLICT (id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at
        """,
        (state_json,),
    )


def add_client_log(conn: sqlite3.Connection, event: str, detail_json: str) -> None:
    conn.execute(
        "INSERT INTO client_logs (event, detail) VALUES (?, ?)", (event, detail_json)
    )
    conn.execute(
        """
        DELETE FROM client_logs WHERE id NOT IN (
            SELECT id FROM client_logs ORDER BY id DESC LIMIT ?
        )
        """,
        (CLIENT_LOGS_MAX_ROWS,),
    )


def list_client_logs(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    """Most recent first, matching how a log viewer is actually read."""
    return conn.execute(
        "SELECT id, created_at, event, detail FROM client_logs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def record_app_event(conn: sqlite3.Connection, event: str) -> None:
    conn.execute("INSERT INTO app_events (event) VALUES (?)", (event,))
    conn.commit()


def record_startup(conn: sqlite3.Connection) -> dict:
    """Insert a startup event, reporting whether the previous process shut down cleanly and how
    long it was down for - lets /version surface unexpected downtime without anyone having to
    export and read the container's own log."""
    prev = conn.execute(
        "SELECT event, at FROM app_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    now = datetime.now(UTC)
    record_app_event(conn, "startup")
    if prev is None:
        return {"clean_previous_shutdown": None, "downtime_seconds": None}
    prev_at = datetime.fromisoformat(prev["at"]).replace(tzinfo=UTC)
    return {
        "clean_previous_shutdown": prev["event"] == "shutdown",
        "downtime_seconds": (now - prev_at).total_seconds(),
    }


def recent_app_events(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    """Most recent first, matching how a log viewer is actually read."""
    return conn.execute(
        "SELECT event, at FROM app_events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def top_loved_artists(
    conn: sqlite3.Connection, min_plays: int = 3, limit: int = 20
) -> list[dict]:
    """Ranks artists by a "love score" (play count x average completion), not just raw play
    count - an artist sampled often but always skipped early (an implicit dislike signal) should
    rank below one played less often but nearly always finished. min_plays filters out noise from
    one-off plays that happen to have landed at 100% or 0%. An artist with an explicit dislike
    on any track is excluded outright - that's a stronger, deliberate signal than a low
    completion rate, and recommend.py uses this list to seed both gap_fill (suggesting more of
    an artist's catalog) and discovery (using these artists as the taste-profile anchor), neither
    of which should be anchored on someone the listener has said they don't like."""
    rows = conn.execute(
        """
        SELECT t.artist AS artist, COUNT(*) AS plays, AVG(pe.percent_listened) AS avg_pct
        FROM play_events pe JOIN tracks t ON t.id = pe.track_id
        WHERE t.artist IS NOT NULL AND t.artist != '' AND t.artist != 'Various Artists'
            AND t.artist NOT IN (
                SELECT DISTINCT artist FROM tracks WHERE disliked_at IS NOT NULL
            )
        GROUP BY t.artist
        HAVING plays >= ?
        ORDER BY plays * avg_pct DESC
        LIMIT ?
        """,
        (min_plays, limit),
    ).fetchall()
    return [
        {"artist": row["artist"], "plays": row["plays"], "avg_pct": row["avg_pct"]}
        for row in rows
    ]


def artist_albums(conn: sqlite3.Connection, artist: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT album FROM tracks WHERE artist = ? AND album IS NOT NULL AND album != ''",
        (artist,),
    ).fetchall()
    return [row["album"] for row in rows]


def top_genres_moods(conn: sqlite3.Connection, limit: int = 10) -> dict:
    genres = conn.execute(
        "SELECT genre, COUNT(*) AS n FROM tracks WHERE genre IS NOT NULL AND genre != '' "
        "GROUP BY genre ORDER BY n DESC LIMIT ?",
        (limit,),
    ).fetchall()
    moods = conn.execute(
        "SELECT mood, COUNT(*) AS n FROM tracks WHERE mood IS NOT NULL AND mood != '' "
        "GROUP BY mood ORDER BY n DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return {
        "genres": [row["genre"] for row in genres],
        "moods": [row["mood"] for row in moods],
    }


def distinct_artists(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT artist FROM tracks WHERE artist IS NOT NULL AND artist != ''"
    ).fetchall()
    return {row["artist"].strip().lower() for row in rows}


def next_recommendation_run_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(run_id), 0) + 1 AS n FROM recommendations"
    ).fetchone()
    return row["n"]


def add_recommendation_run(
    conn: sqlite3.Connection, run_id: int, items: list[dict]
) -> None:
    """Append-only - see the table's own comment in SCHEMA for why."""
    conn.executemany(
        "INSERT INTO recommendations (run_id, category, artist, reasoning, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (
                run_id,
                item["category"],
                item["artist"],
                item.get("reasoning"),
                item.get("detail"),
            )
            for item in items
        ],
    )


def list_recommendations(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Only the latest run - history exists for find_gap_fill/find_discovery's own exclusion
    logic (see backend/recommend.py), not to show stale suggestions in the UI."""
    return conn.execute(
        "SELECT id, created_at, category, artist, reasoning, detail, status FROM recommendations "
        "WHERE run_id = (SELECT MAX(run_id) FROM recommendations) ORDER BY category, id"
    ).fetchall()


def set_recommendation_status(
    conn: sqlite3.Connection, rec_id: int, status: str
) -> None:
    conn.execute("UPDATE recommendations SET status=? WHERE id=?", (status, rec_id))


def previously_recommended_artists(conn: sqlite3.Connection, category: str) -> set[str]:
    """Every artist ever recommended under this category, across all past runs - not just the
    latest - so a rotating seed (see backend/recommend.py) doesn't just re-surface the same
    suggestion every few runs once it cycles back around."""
    rows = conn.execute(
        "SELECT DISTINCT artist FROM recommendations WHERE category = ?", (category,)
    ).fetchall()
    return {row["artist"].strip().lower() for row in rows}


def decided_recommendation_artists(conn: sqlite3.Connection, category: str) -> set[str]:
    """Artists explicitly accepted or dismissed under this category - excluded from future
    candidate generation entirely (see backend/recommend.py's _rotate_seed), unlike a merely
    "already suggested" artist which can still resurface once the fresh pool runs dry."""
    rows = conn.execute(
        "SELECT DISTINCT artist FROM recommendations WHERE category = ? AND status != 'pending'",
        (category,),
    ).fetchall()
    return {row["artist"].strip().lower() for row in rows}
