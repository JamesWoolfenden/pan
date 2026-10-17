"""One-off migration: un-corrupt a compilation album whose artist tag was set to the compiler
(e.g. "Santana", "Various Artists") for every track, with the real artist and title instead
crammed into the title field as "NNN - Real Artist - Real Title" (or "Real Artist - Real Title"
without the leading number).

Confirmed against "Bacobens Rock Top 500", a 504-track rock compilation where this made ~280
songs (The Rolling Stones, Neil Young, Derek and the Dominos, etc.) invisible to search and
lyrics lookup under their real artist - found via reports of "only one track for the Rolling
Stones" and tracks with no lyrics that turned out to have garbage title/artist data.

Safe to re-run: a title with no " - " separator (nothing to split, e.g. Santana's own actual
"Black Magic Woman" in this same album) is left untouched.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import sys

from backend.db import connection, get_track, set_artist, set_title
from backend.search.embeddings import reindex_embeddings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("split_compilation_titles")

DEFAULT_ALBUM = "Bacobens Rock Top 500"

_NUMBERED = re.compile(r"^\d+\s*-\s*(.+?) - (.+)$")
_PLAIN = re.compile(r"^(.+?) - (.+)$")
_STRAY_LEADING_NUMBER = re.compile(r"^\d+\s*-\s*(.+)$")


def _split_title(title: str) -> tuple[str, str] | None:
    """ "NNN - Real Artist - Real Title" or "Real Artist - Real Title" -> (artist, title).
    Requires " - " (spaces around the dash) as the separator, not a bare hyphen, so a name like
    "T-Rex" doesn't get split mid-word. Returns None if the title has no such separator."""
    match = _NUMBERED.match(title) or _PLAIN.match(title)
    if not match:
        return None
    artist, clean_title = match.group(1).strip(), match.group(2).strip()
    # Some entries double up the leading position number inside the already-extracted title
    # (e.g. "320 - Derek and the Dominos - 10 - Bell Bottom Blues") - strip that too.
    stray = _STRAY_LEADING_NUMBER.match(clean_title)
    if stray:
        clean_title = stray.group(1).strip()
    return artist, clean_title


def migrate(conn: sqlite3.Connection, album: str) -> dict:
    counts = {"fixed": 0, "no_separator_skipped": 0}
    rows = conn.execute(
        "SELECT id, title FROM tracks WHERE album = ?", (album,)
    ).fetchall()
    for row in rows:
        split = _split_title(row["title"] or "")
        if split is None:
            counts["no_separator_skipped"] += 1
            continue
        artist, title = split
        set_artist(conn, row["id"], artist)
        set_title(conn, row["id"], title)
        counts["fixed"] += 1
    return counts


def main() -> None:
    album = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ALBUM
    with connection() as conn:
        counts = migrate(conn, album)
        conn.commit()
        rows = conn.execute(
            "SELECT id FROM tracks WHERE album = ?", (album,)
        ).fetchall()
        fixed_rows = [get_track(conn, row["id"]) for row in rows]
        reindex_embeddings(conn, fixed_rows)

    log.info(
        "Fixed %d tracks in %r (re-embedded), left %d untouched (no ' - ' separator)",
        counts["fixed"],
        album,
        counts["no_separator_skipped"],
    )


if __name__ == "__main__":
    main()
