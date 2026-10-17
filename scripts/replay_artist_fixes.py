"""One-off: replay this session's "Various Artists" -> real artist corrections against another
copy of the database (the NAS's live pan.db), since those corrections were made against the
local dev DB and there's no safe generic way to merge two independently-diverging SQLite files.

Matches by (album, title) rather than id - the two databases may not share row ids for the same
track if either has run a reindex since they diverged, but album/title text is stable either way.

Usage (from the NAS, via the pan container's Terminal tab in Container Manager):
    uv run python -m scripts.replay_artist_fixes

Safe to re-run: a track already matching the target artist is simply skipped.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.db import connection, get_track, set_artist
from backend.search.embeddings import reindex_embeddings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("replay_artist_fixes")

DATA_FILE = Path(__file__).parent / "_artist_fix_replay_data.json"


def main() -> None:
    fixes = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    with connection() as conn:
        updated = []
        already_correct = 0
        not_found = []
        for fix in fixes:
            row = conn.execute(
                "SELECT id, artist FROM tracks WHERE album = ? AND title = ?",
                (fix["album"], fix["title"]),
            ).fetchone()
            if row is None:
                not_found.append(f"{fix['title']} ({fix['album']})")
                continue
            if row["artist"] == fix["artist"]:
                already_correct += 1
                continue
            set_artist(conn, row["id"], fix["artist"])
            updated.append(row["id"])
        conn.commit()

        if updated:
            reindex_embeddings(conn, [get_track(conn, tid) for tid in updated])

    log.info(
        "Updated %d tracks, %d already correct, %d not found",
        len(updated),
        already_correct,
        len(not_found),
    )
    if not_found:
        log.warning("Not found on this DB (title/album mismatch?): %s", not_found)


if __name__ == "__main__":
    main()
