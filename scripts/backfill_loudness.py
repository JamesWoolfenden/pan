"""One-off backfill: measure loudness_lufs for tracks that already have tempo/energy set (added
before loudness_lufs existed, so the normal reindex feature-extraction pass - which only looks at
tracks where tempo IS NULL - never revisits them). New tracks get loudness_lufs for free as part
of that same pass from now on; this is purely to catch up the existing catalog.

Re-decodes each file once (same cost as the original tempo/energy extraction pass) - expect this
to take as long as that did for the full library. Safe to re-run/interrupt: only tracks still
missing loudness_lufs are selected each time, and progress commits periodically.
"""

from __future__ import annotations

import logging
import time

from backend.db import all_tracks, connection, set_audio_features
from backend.paths import resolve as resolve_file_path
from backend.tagging.audio_features import extract_features

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("backfill_loudness")

COMMIT_EVERY = 100
LOG_EVERY = 500


def main() -> None:
    with connection() as conn:
        to_process = [
            row
            for row in all_tracks(conn)
            if row["loudness_lufs"] is None
            and row["tempo"] is not None
            and row["file_path"]
            and row["source"] != "local_video"
        ]
        log.info("%d tracks missing loudness_lufs", len(to_process))

        measured = 0
        start = time.monotonic()
        for processed, row in enumerate(to_process, start=1):
            features = extract_features(
                resolve_file_path(row["file_path"], row["source"])
            )
            if features and features["loudness_lufs"] is not None:
                set_audio_features(
                    conn,
                    row["id"],
                    row["tempo"],
                    row["energy"],
                    row["musical_key"],
                    features["loudness_lufs"],
                )
                measured += 1

            if processed % COMMIT_EVERY == 0:
                conn.commit()
            if processed % LOG_EVERY == 0 or processed == len(to_process):
                elapsed = time.monotonic() - start
                rate = processed / elapsed if elapsed else 0
                remaining = (
                    (len(to_process) - processed) / rate if rate else float("inf")
                )
                log.info(
                    "Loudness: %d/%d processed (%d measured) - %.1f tracks/s, ~%.0fs remaining",
                    processed,
                    len(to_process),
                    measured,
                    rate,
                    remaining,
                )
        conn.commit()
        log.info("Measured loudness for %d/%d tracks", measured, len(to_process))


if __name__ == "__main__":
    main()
