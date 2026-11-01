"""Scrobbles plays to Last.fm. Called from the play-event endpoint: a play that meets Last.fm's
rules is queued in the DB (scrobble_queue) and the queue is then flushed, so plays made while
Last.fm or the network is down are retried on the next play instead of lost. A no-op unless the
LASTFM_* scrobbling settings are configured (see backend/lastfm.py).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time

from backend import lastfm
from backend.db import connection

log = logging.getLogger(__name__)

BATCH_SIZE = 50  # Last.fm's per-request limit
MIN_TRACK_SECONDS = 30  # shorter tracks are never scrobbled
MAX_REQUIRED_SECONDS = (
    240  # ...played for half its length or 4 minutes, whichever is first
)

_flush_lock = threading.Lock()


def is_scrobblable(duration_ms: int | None, percent_listened: float) -> bool:
    """Last.fm's rule: track longer than 30s, and listened to for at least half of it or 4
    minutes, whichever comes first."""
    if not duration_ms:
        return False
    duration_s = duration_ms / 1000
    if duration_s <= MIN_TRACK_SECONDS:
        return False
    return percent_listened * duration_s >= min(duration_s / 2, MAX_REQUIRED_SECONDS)


def enqueue_play(
    conn: sqlite3.Connection, track: sqlite3.Row, percent_listened: float
) -> bool:
    """Queues the play if scrobbling is on and it qualifies. Returns whether it was queued. The
    timestamp is the play's start (now minus the time actually listened), as Last.fm expects."""
    if not lastfm.scrobbling_enabled():
        return False
    if not track["artist"] or not track["title"]:
        return False
    if not is_scrobblable(track["duration_ms"], percent_listened):
        return False
    duration_s = round(track["duration_ms"] / 1000)
    started = int(time.time() - percent_listened * track["duration_ms"] / 1000)
    conn.execute(
        "INSERT INTO scrobble_queue (artist, track, album, duration_s, timestamp)"
        " VALUES (?, ?, ?, ?, ?)",
        (track["artist"], track["title"], track["album"], duration_s, started),
    )
    return True


def flush_queue() -> None:
    """Sends queued plays to Last.fm in batches, deleting those accepted. Stops at the first
    batch that should be retried later. Safe to call concurrently (one flush at a time)."""
    if not lastfm.scrobbling_enabled() or not _flush_lock.acquire(blocking=False):
        return
    try:
        with connection() as conn:
            while True:
                rows = conn.execute(
                    "SELECT * FROM scrobble_queue ORDER BY id LIMIT ?", (BATCH_SIZE,)
                ).fetchall()
                if not rows:
                    return
                entries = [
                    {
                        "artist": r["artist"],
                        "track": r["track"],
                        "album": r["album"],
                        "duration": r["duration_s"],
                        "timestamp": r["timestamp"],
                    }
                    for r in rows
                ]
                if not lastfm.scrobble(entries):
                    return
                conn.executemany(
                    "DELETE FROM scrobble_queue WHERE id = ?",
                    [(r["id"],) for r in rows],
                )
                conn.commit()
    finally:
        _flush_lock.release()
