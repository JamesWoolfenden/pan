from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from backend.config import settings
from backend.db import all_tracks, delete_track, merge_plex_metadata, upsert_track
from backend.ingest.local_source import (
    VIDEO_EXTENSIONS,
    iter_local_tracks,
    normalize_path,
)
from backend.ingest.plex_source import iter_plex_tracks
from backend.paths import resolve as resolve_file_path

log = logging.getLogger(__name__)

COMMIT_EVERY = 100

# Refuses to prune more than this fraction of a source's tracks in one run. A real cleanup
# (a handful of bad rips removed, an album deleted) is always a small fraction of the catalog -
# a number anywhere near this threshold means the scan itself was bad (Plex temporarily
# unreachable/empty, the shared drive unmounted mid-run), not that the library actually shrank,
# and deleting on that basis would wipe out most of the catalog instead of a few stale rows.
PRUNE_SAFETY_THRESHOLD = 0.2


def prune_missing_plex_tracks(
    conn: sqlite3.Connection, seen_external_ids: set[str]
) -> int:
    """Deletes 'plex'-sourced tracks this run's scan didn't see - removed, moved, or a bad
    duplicate cleaned up in Plex's own library since the last reindex. Only call this after a
    full, successful scan of every Plex music section: seen_external_ids being incomplete (a
    partial/interrupted scan) is indistinguishable here from the tracks genuinely being gone, and
    would wrongly delete everything the scan didn't get to."""
    rows = conn.execute(
        "SELECT id, external_id FROM tracks WHERE source = 'plex'"
    ).fetchall()
    if not rows:
        return 0
    stale_ids = [
        row["id"] for row in rows if row["external_id"] not in seen_external_ids
    ]
    if len(stale_ids) > len(rows) * PRUNE_SAFETY_THRESHOLD:
        log.warning(
            "Refusing to prune %d/%d Plex tracks (over the %.0f%% safety threshold) - "
            "this looks like a bad or partial Plex scan rather than real removals; "
            "investigate before reindexing again",
            len(stale_ids),
            len(rows),
            PRUNE_SAFETY_THRESHOLD * 100,
        )
        return 0
    for track_id in stale_ids:
        delete_track(conn, track_id)
    return len(stale_ids)


def prune_missing_local_tracks(conn: sqlite3.Connection) -> int:
    """Deletes 'local'/'local_video' tracks whose file no longer exists on disk. Checked
    individually against the filesystem (unlike Plex tracks, there's no single authoritative
    listing to diff against - iter_local_tracks only yields files not already known, via
    skip_paths, so it can't tell "still there" apart from "never seen"). Skipped entirely if
    SHARED_DRIVE_PATH isn't configured or isn't currently reachable - a disconnected network
    share would otherwise make every local file look deleted."""
    if not settings.shared_drive_path or not Path(settings.shared_drive_path).exists():
        log.warning(
            "SHARED_DRIVE_PATH unreachable - skipping local-track pruning this run"
        )
        return 0
    rows = conn.execute(
        "SELECT id, file_path, source FROM tracks WHERE source IN ('local', 'local_video')"
    ).fetchall()
    if not rows:
        return 0
    stale_ids = [
        row["id"]
        for row in rows
        if row["file_path"]
        and not resolve_file_path(row["file_path"], row["source"]).exists()
    ]
    if len(stale_ids) > len(rows) * PRUNE_SAFETY_THRESHOLD:
        log.warning(
            "Refusing to prune %d/%d local tracks (over the %.0f%% safety threshold) - "
            "this looks like the shared drive being unreachable rather than real removals; "
            "investigate before reindexing again",
            len(stale_ids),
            len(rows),
            PRUNE_SAFETY_THRESHOLD * 100,
        )
        return 0
    for track_id in stale_ids:
        delete_track(conn, track_id)
    return len(stale_ids)


def known_file_paths(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT file_path, source FROM tracks WHERE file_path IS NOT NULL"
    ).fetchall()
    return {normalize_path(row["file_path"], row["source"]) for row in rows}


def path_to_id_map(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        normalize_path(row["file_path"], row["source"]): row["id"]
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
    counts = {"plex": 0, "plex_merged": 0, "local": 0, "video": 0, "pruned": 0}

    if settings.plex_baseurl and settings.plex_token:
        existing_by_path = path_to_id_map(conn)
        seen_external_ids: set[str] = set()
        for track in iter_plex_tracks(settings.plex_baseurl, settings.plex_token):
            seen_external_ids.add(track.external_id)
            existing_id = (
                existing_by_path.get(normalize_path(track.file_path))
                if track.file_path
                else None
            )
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
        log.info(
            "Plex: %d tracks seen, %d merged into existing rows, %d inserted new",
            counts["plex"],
            counts["plex_merged"],
            counts["plex"] - counts["plex_merged"],
        )
        # Only reached once the scan above has run to completion without raising - see
        # prune_missing_plex_tracks's docstring for why a partial scan must never get here.
        pruned = prune_missing_plex_tracks(conn, seen_external_ids)
        conn.commit()
        if pruned:
            log.info("Pruned %d Plex track(s) no longer in Plex's library", pruned)
        counts["pruned"] += pruned
    else:
        log.info("Plex not configured (PLEX_BASEURL/PLEX_TOKEN missing) - skipping")

    if settings.shared_drive_path:
        skip_paths = known_file_paths(conn)
        for track in iter_local_tracks(
            settings.shared_drive_path, skip_paths=skip_paths
        ):
            upsert_track(conn, track)
            counts["local"] += 1
            if counts["local"] % COMMIT_EVERY == 0:
                conn.commit()
        conn.commit()
    else:
        log.info("SHARED_DRIVE_PATH not configured - skipping local scan")

    # A separate root, scanned for video only - for when video files live on a genuinely
    # different drive/share than the music library, rather than in a subfolder of
    # SHARED_DRIVE_PATH (which is already covered above, no separate config needed for that).
    if settings.video_drive_path:
        skip_paths = known_file_paths(conn)
        for track in iter_local_tracks(
            settings.video_drive_path,
            skip_paths=skip_paths,
            extensions=VIDEO_EXTENSIONS,
        ):
            upsert_track(conn, track)
            counts["video"] += 1
            if counts["video"] % COMMIT_EVERY == 0:
                conn.commit()
        conn.commit()

    pruned = prune_missing_local_tracks(conn)
    conn.commit()
    if pruned:
        log.info("Pruned %d local track(s) whose file no longer exists", pruned)
    counts["pruned"] += pruned

    return counts
