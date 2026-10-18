from __future__ import annotations

import sqlite3

from backend.db import tags_list


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
        "favorite": bool(row["favorited_at"]) if "favorited_at" in keys else False,
        "tags": tags_list(row) if "tags" in keys else [],
    }


def most_played(
    conn: sqlite3.Connection, limit: int = 25, offset: int = 0
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT t.*, COUNT(pe.id) AS play_count, AVG(pe.percent_listened) AS avg_percent,
               MAX(pe.played_at) AS last_played_at
        FROM tracks t
        JOIN play_events pe ON pe.track_id = t.id
        GROUP BY t.id
        ORDER BY play_count DESC, last_played_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return [_to_stat_result(row) for row in rows]


def never_played(
    conn: sqlite3.Connection, limit: int = 25, offset: int = 0
) -> list[dict]:
    """Tracks with zero play events, playable ones only (no reachable file means it literally
    can't have been played - that's not an interesting "never played" signal)."""
    rows = conn.execute(
        """
        SELECT t.* FROM tracks t
        LEFT JOIN play_events pe ON pe.track_id = t.id
        WHERE pe.id IS NULL AND t.file_path IS NOT NULL
        ORDER BY t.artist, t.title
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return [_to_stat_result(row) for row in rows]


def recently_played(
    conn: sqlite3.Connection, limit: int = 25, offset: int = 0
) -> list[dict]:
    """Distinct tracks ordered by their most recent play, newest first - "recently played",
    not a raw play-event log (a track heard 3 times today would otherwise show 3 times).
    played_at only has 1-second resolution, so two plays in the same second (e.g. in tests, or
    a user rapid-skipping) tie - break ties by the later event's id, the actual insertion order."""
    rows = conn.execute(
        """
        SELECT t.*, COUNT(pe.id) AS play_count, AVG(pe.percent_listened) AS avg_percent,
               MAX(pe.played_at) AS last_played_at, MAX(pe.id) AS last_played_event_id
        FROM tracks t
        JOIN play_events pe ON pe.track_id = t.id
        GROUP BY t.id
        ORDER BY last_played_at DESC, last_played_event_id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return [_to_stat_result(row) for row in rows]


def recently_added(
    conn: sqlite3.Connection, limit: int = 25, offset: int = 0
) -> list[dict]:
    """Most recently added tracks, newest first. There's no separate "added at" timestamp column
    - id is AUTOINCREMENT and never changes once a row exists (upsert_track only UPDATEs matching
    rows), so insertion order is exactly id order, including for rows ingested before this stat
    existed."""
    rows = conn.execute(
        "SELECT * FROM tracks ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
    return [_to_stat_result(row) for row in rows]


def favorites(
    conn: sqlite3.Connection, limit: int = 200, offset: int = 0
) -> list[dict]:
    """Favorited tracks, most recently favorited first. favorited_at ties (possible since it
    comes from Python's clock, which can be coarser than a microsecond on some systems) break
    by id descending, the actual order favorites were set in."""
    rows = conn.execute(
        "SELECT * FROM tracks WHERE favorited_at IS NOT NULL "
        "ORDER BY favorited_at DESC, id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [_to_stat_result(row) for row in rows]


def most_skipped(
    conn: sqlite3.Connection, limit: int = 25, offset: int = 0
) -> list[dict]:
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
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return [_to_stat_result(row) for row in rows]
