"""One-off migration: rewrite any remaining absolute file_path values (from before paths were
stored relative to SHARED_DRIVE_PATH) to relative ones, so the whole catalog - not just rows
added since the refactor - is portable to another machine (e.g. the NAS) with a different root.

Safe to re-run: rows that are already relative, or absolute but not under SHARED_DRIVE_PATH
(left untouched - there's no meaningful relative form for those), are simply skipped.
"""

from __future__ import annotations

import logging
import sqlite3

from backend.config import settings
from backend.db import all_tracks, connection, set_file_path
from backend.paths import is_absolute, to_relative

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("migrate_relative_paths")


def migrate(conn: sqlite3.Connection, shared_drive_path: str) -> dict:
    counts = {"converted": 0, "already_relative": 0, "skipped_not_under_root": 0}

    for row in all_tracks(conn):
        path = row["file_path"]
        if not path:
            continue
        if not is_absolute(path):
            counts["already_relative"] += 1
            continue

        relative = to_relative(path, shared_drive_path)
        if relative == path:
            # to_relative() falls back to returning the input unchanged when it isn't actually
            # under the root - nothing meaningful to convert.
            counts["skipped_not_under_root"] += 1
            continue

        set_file_path(conn, row["id"], relative)
        counts["converted"] += 1

    return counts


def main() -> None:
    if not settings.shared_drive_path:
        log.error(
            "SHARED_DRIVE_PATH is not configured - nothing to make paths relative to"
        )
        return

    with connection() as conn:
        counts = migrate(conn, settings.shared_drive_path)

    log.info(
        "Converted %d absolute paths to relative, %d already relative, %d left absolute (not under %s)",
        counts["converted"],
        counts["already_relative"],
        counts["skipped_not_under_root"],
        settings.shared_drive_path,
    )


if __name__ == "__main__":
    main()
