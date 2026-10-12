from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

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
    ai_background TEXT  -- cached AI-generated "tell me more" writeup, so it's only generated once
);
CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);
CREATE INDEX IF NOT EXISTS idx_tracks_genre ON tracks(genre);

CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
"""


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
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()}
    if "ai_background" not in existing_columns:
        conn.execute("ALTER TABLE tracks ADD COLUMN ai_background TEXT")


def upsert_track(conn: sqlite3.Connection, track: Track) -> int:
    existing = conn.execute(
        "SELECT id, tag_source FROM tracks WHERE external_id = ?", (track.external_id,)
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE tracks SET source=?, plex_rating_key=?, file_path=?, title=?, artist=?,
                album=?, genre=COALESCE(?, genre), mood=COALESCE(?, mood), year=?,
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


def set_tags(conn: sqlite3.Connection, track_id: int, genre: str | None, mood: str | None, tag_source: str) -> None:
    conn.execute(
        "UPDATE tracks SET genre=?, mood=?, tag_source=? WHERE id=?",
        (genre, mood, tag_source, track_id),
    )


def set_audio_features(conn: sqlite3.Connection, track_id: int, tempo: float, energy: float, musical_key: str) -> None:
    conn.execute(
        "UPDATE tracks SET tempo=?, energy=?, musical_key=? WHERE id=?",
        (tempo, energy, musical_key, track_id),
    )


def set_ai_background(conn: sqlite3.Connection, track_id: int, text: str) -> None:
    conn.execute("UPDATE tracks SET ai_background=? WHERE id=?", (text, track_id))


def set_file_path(conn: sqlite3.Connection, track_id: int, file_path: str) -> None:
    conn.execute("UPDATE tracks SET file_path=? WHERE id=?", (file_path, track_id))


def merge_plex_metadata(conn: sqlite3.Connection, track_id: int, track: Track) -> None:
    """Enrich an already-cataloged track (e.g. found by the local scan) with Plex's metadata,
    without touching its id - so audio features/embeddings already computed for it survive."""
    conn.execute(
        """
        UPDATE tracks SET
            plex_rating_key = ?,
            source = 'plex',
            title = COALESCE(NULLIF(?, ''), title),
            artist = COALESCE(NULLIF(?, ''), artist),
            album = COALESCE(NULLIF(?, ''), album),
            genre = COALESCE(?, genre),
            mood = COALESCE(?, mood),
            year = COALESCE(?, year)
        WHERE id = ?
        """,
        (track.plex_rating_key, track.title, track.artist, track.album,
         track.genre, track.mood, track.year, track_id),
    )


def tracks_missing_tags(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM tracks WHERE (genre IS NULL OR mood IS NULL) AND tag_source != 'cloud'"
    ).fetchall()


def all_tracks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tracks").fetchall()


def get_track(conn: sqlite3.Connection, track_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()


def get_tracks_by_ids(conn: sqlite3.Connection, ids: list[int]) -> list[sqlite3.Row]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(f"SELECT * FROM tracks WHERE id IN ({placeholders})", ids).fetchall()
    by_id = {row["id"]: row for row in rows}
    return [by_id[i] for i in ids if i in by_id]


def upsert_embedding(conn: sqlite3.Connection, track_id: int, embedding: list[float]) -> None:
    conn.execute("DELETE FROM track_vectors WHERE rowid = ?", (track_id,))
    conn.execute(
        "INSERT INTO track_vectors (rowid, embedding) VALUES (?, ?)",
        (track_id, sqlite_vec.serialize_float32(embedding)),
    )


def search_embeddings(conn: sqlite3.Connection, query_embedding: list[float], limit: int = 25) -> list[tuple[int, float]]:
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


def find_similar_embeddings(conn: sqlite3.Connection, track_id: int, limit: int = 25) -> list[tuple[int, float]]:
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


def add_track_to_playlist(conn: sqlite3.Connection, playlist_id: int, track_id: int) -> None:
    next_position = conn.execute(
        "SELECT COALESCE(MAX(position) + 1, 0) AS n FROM playlist_tracks WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()["n"]
    conn.execute(
        "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
        (playlist_id, track_id, next_position),
    )


def list_playlists(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.id, p.name, p.created_at, COUNT(pt.track_id) AS track_count
        FROM playlists p
        LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
        GROUP BY p.id
        ORDER BY p.created_at DESC
        """
    ).fetchall()


def get_playlist(conn: sqlite3.Connection, playlist_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()


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


def record_play_event(conn: sqlite3.Connection, track_id: int, percent_listened: float) -> None:
    percent_listened = max(0.0, min(1.0, percent_listened))
    conn.execute(
        "INSERT INTO play_events (track_id, percent_listened) VALUES (?, ?)",
        (track_id, percent_listened),
    )
