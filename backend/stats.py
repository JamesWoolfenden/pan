from __future__ import annotations

import sqlite3


def _to_stat_result(row: sqlite3.Row) -> dict:
    keys = row.keys()
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
        "play_count": row["play_count"] if "play_count" in keys else 0,
        "avg_percent_listened": row["avg_percent"] if "avg_percent" in keys else None,
        "last_played_at": row["last_played_at"] if "last_played_at" in keys else None,
    }


def most_played(conn: sqlite3.Connection, limit: int = 25) -> list[dict]:
    rows = conn.execute(
        """
        SELECT t.*, COUNT(pe.id) AS play_count, AVG(pe.percent_listened) AS avg_percent,
               MAX(pe.played_at) AS last_played_at
        FROM tracks t
        JOIN play_events pe ON pe.track_id = t.id
        GROUP BY t.id
        ORDER BY play_count DESC, last_played_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_to_stat_result(row) for row in rows]


def never_played(conn: sqlite3.Connection, limit: int = 25) -> list[dict]:
    """Tracks with zero play events, playable ones only (no reachable file means it literally
    can't have been played - that's not an interesting "never played" signal)."""
    rows = conn.execute(
        """
        SELECT t.* FROM tracks t
        LEFT JOIN play_events pe ON pe.track_id = t.id
        WHERE pe.id IS NULL AND t.file_path IS NOT NULL
        ORDER BY t.artist, t.title
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_to_stat_result(row) for row in rows]


def most_skipped(conn: sqlite3.Connection, limit: int = 25) -> list[dict]:
    """Tracks played at least twice with a low average percent listened - a real "didn't like
    it" signal, as opposed to a single interrupted play or a track that's simply undiscovered."""
    rows = conn.execute(
        """
        SELECT t.*, COUNT(pe.id) AS play_count, AVG(pe.percent_listened) AS avg_percent,
               MAX(pe.played_at) AS last_played_at
        FROM tracks t
        JOIN play_events pe ON pe.track_id = t.id
        GROUP BY t.id
        HAVING play_count >= 2
        ORDER BY avg_percent ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_to_stat_result(row) for row in rows]
