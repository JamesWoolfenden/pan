from __future__ import annotations

import logging
import sqlite3

from backend.config import settings
from backend.db import all_tracks, merge_plex_metadata, upsert_track
from backend.ingest.local_source import iter_local_tracks, normalize_path
from backend.ingest.plex_source import iter_plex_tracks

log = logging.getLogger(__name__)

COMMIT_EVERY = 100


def known_file_paths(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT file_path FROM tracks WHERE file_path IS NOT NULL").fetchall()
    return {normalize_path(row["file_path"]) for row in rows}


def path_to_id_map(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        normalize_path(row["file_path"]): row["id"]
        for row in all_tracks(conn)
        if row["file_path"]
    }


def run_ingest(conn: sqlite3.Connection) -> dict:
    """Pull Plex first (it's the richer metadata source), then scan the shared drive for
    anything Plex doesn't already know about by file path, to avoid duplicate catalog entries
    for the same physical file.

    Plex tracks are matched against existing rows by (remapped) file path, not just Plex's own
    ratingKey: if PLEX_REMOTE_MUSIC_PATH is configured and a Plex track's file resolves to a
    path the local scan already cataloged, that existing row is enriched in place (preserving
    its id, and therefore any audio features/embeddings already computed for it) rather than
    inserted as a second, duplicate, unplayable row.

    Commits periodically rather than in one long transaction, both so progress survives an
    interruption and so this doesn't hold a write lock that starves another process (e.g. a
    concurrently-running feature-extraction pass) of its own periodic commits."""
    counts = {"plex": 0, "plex_merged": 0, "local": 0}

    if settings.plex_baseurl and settings.plex_token:
        existing_by_path = path_to_id_map(conn)
        for track in iter_plex_tracks(settings.plex_baseurl, settings.plex_token):
            existing_id = existing_by_path.get(normalize_path(track.file_path)) if track.file_path else None
            if existing_id is not None:
                merge_plex_metadata(conn, existing_id, track)
                counts["plex_merged"] += 1
            else:
                new_id = upsert_track(conn, track)
                if track.file_path:
                    existing_by_path[normalize_path(track.file_path)] = new_id
            counts["plex"] += 1
            if counts["plex"] % COMMIT_EVERY == 0:
                conn.commit()
        conn.commit()
        log.info("Plex: %d tracks seen, %d merged into existing rows, %d inserted new",
                  counts["plex"], counts["plex_merged"], counts["plex"] - counts["plex_merged"])
    else:
        log.info("Plex not configured (PLEX_BASEURL/PLEX_TOKEN missing) - skipping")

    if settings.shared_drive_path:
        skip_paths = known_file_paths(conn)
        for track in iter_local_tracks(settings.shared_drive_path, skip_paths=skip_paths):
            upsert_track(conn, track)
            counts["local"] += 1
            if counts["local"] % COMMIT_EVERY == 0:
                conn.commit()
        conn.commit()
    else:
        log.info("SHARED_DRIVE_PATH not configured - skipping local scan")

    return counts
