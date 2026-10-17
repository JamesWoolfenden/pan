"""Full rescan + retag + reindex: pulls the catalog from Plex and the shared drive, extracts
audio features, fills tagging gaps (local heuristics, then Claude for what's left), and
rebuilds the search embedding index. Safe to re-run; it's all upsert-based."""

from __future__ import annotations

import logging
import time

from backend.db import (
    all_tracks,
    connection,
    init_db,
    set_audio_features,
    tracks_missing_tags,
)
from backend.ingest.merge import run_ingest
from backend.paths import resolve as resolve_file_path
from backend.search.embeddings import reindex_embeddings
from backend.tagging.audio_features import extract_features
from backend.tagging.cloud_tagger import tag_with_cloud
from backend.tagging.local_tagger import apply_local_tags

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("reindex")

COMMIT_EVERY = 100
LOG_EVERY = 500


def main() -> None:
    with connection() as conn:
        init_db(conn)

        counts = run_ingest(conn)
        log.info(
            "Ingested %d Plex tracks, %d local-only tracks",
            counts["plex"],
            counts["local"],
        )

        # Audio analysis is by far the slowest phase on a large library, and can run for
        # hours. Commit + log progress periodically rather than only at the end, so a crash
        # or interruption doesn't lose all of it, and so progress is observable from outside.
        to_process = [
            row for row in all_tracks(conn) if row["tempo"] is None and row["file_path"]
        ]
        feature_count = 0
        start = time.monotonic()
        for processed, row in enumerate(to_process, start=1):
            features = extract_features(resolve_file_path(row["file_path"]))
            if features:
                set_audio_features(
                    conn,
                    row["id"],
                    features["tempo"],
                    features["energy"],
                    features["musical_key"],
                )
                feature_count += 1

            if processed % COMMIT_EVERY == 0:
                conn.commit()
            if processed % LOG_EVERY == 0 or processed == len(to_process):
                elapsed = time.monotonic() - start
                rate = processed / elapsed if elapsed else 0
                remaining = (
                    (len(to_process) - processed) / rate if rate else float("inf")
                )
                log.info(
                    "Audio features: %d/%d processed (%d succeeded) - %.1f tracks/s, ~%.0fs remaining",
                    processed,
                    len(to_process),
                    feature_count,
                    rate,
                    remaining,
                )
        conn.commit()
        log.info(
            "Extracted audio features for %d/%d tracks", feature_count, len(to_process)
        )

        candidates = tracks_missing_tags(conn)
        still_missing = []
        for row in candidates:
            if not apply_local_tags(
                conn, row["id"], row["genre"], row["mood"], row["tempo"], row["energy"]
            ):
                still_missing.append(row)
        conn.commit()
        log.info(
            "Local tagging resolved %d/%d tracks, %d still need cloud tagging",
            len(candidates) - len(still_missing),
            len(candidates),
            len(still_missing),
        )

        cloud_tagged = tag_with_cloud(conn, still_missing)
        log.info("Cloud-tagged %d tracks", cloud_tagged)

        indexed = reindex_embeddings(conn, all_tracks(conn))
        log.info("Rebuilt embeddings for %d tracks", indexed)


if __name__ == "__main__":
    main()
