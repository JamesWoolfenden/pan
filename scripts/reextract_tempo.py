"""One-off full-library pass: re-extract tempo/energy/key for every track that already has a
value, not just ones missing it (scripts/reindex.py's audio-feature step only fills gaps).

Needed because the tempo extraction used before this fix (hop_length=512, the librosa default)
quantized BPM onto a coarse ~14-value grid shared across the whole library - e.g. 1302 of 19191
tracks landed on the exact same 143.5546875 BPM - which made "Similar" rank wildly unrelated
tracks as "identical tempo" and drown out genre. hop_length=128 (see backend/tagging/
audio_features.py) fixes new extractions; this script redoes the old ones.

Parallelized across CPU cores since librosa's beat tracking is pure CPU work with no shared
state - each worker just reads one audio file and returns numbers. DB writes happen sequentially
in the main process so SQLite isn't hit with concurrent writers.

Usage:
    uv run python -m scripts.reextract_tempo --limit 300      # trial run on a few hundred tracks
    uv run python -m scripts.reextract_tempo --workers 10     # full library on a big machine

The default is 2 workers, safe on a NAS CPU alongside the running server. Local video rows are
skipped: tempo/energy/key are music-specific heuristics (see reindex.py).
"""

from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import ProcessPoolExecutor

from backend.db import connection, init_db, set_audio_features
from backend.paths import resolve as resolve_file_path
from backend.tagging.audio_features import extract_features

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("reextract_tempo")

COMMIT_EVERY = 50
LOG_EVERY = 200
DEFAULT_WORKERS = 2


def _extract(job: tuple[int, str]) -> tuple[int, dict | None]:
    track_id, file_path = job
    return track_id, extract_features(resolve_file_path(file_path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("Usage:")[0].strip())
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Parallel decode processes (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N tracks (by id) - for a trial run",
    )
    args = parser.parse_args()
    workers = max(1, args.workers)

    with connection() as conn:
        init_db(conn)
        rows = conn.execute(
            "SELECT id, file_path FROM tracks "
            "WHERE file_path IS NOT NULL AND source != 'local_video' ORDER BY id"
            + (" LIMIT ?" if args.limit else ""),
            (args.limit,) if args.limit else (),
        ).fetchall()
        jobs = [(row["id"], row["file_path"]) for row in rows]

    log.info(
        "Re-extracting audio features for %d tracks across %d workers",
        len(jobs),
        workers,
    )
    succeeded = 0
    failed = 0
    start = time.monotonic()

    with connection() as conn:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for processed, (track_id, features) in enumerate(
                pool.map(_extract, jobs, chunksize=4), start=1
            ):
                if features:
                    set_audio_features(
                        conn,
                        track_id,
                        features["tempo"],
                        features["energy"],
                        features["musical_key"],
                        features["loudness_lufs"],
                    )
                    succeeded += 1
                else:
                    failed += 1

                if processed % COMMIT_EVERY == 0:
                    conn.commit()
                if processed % LOG_EVERY == 0 or processed == len(jobs):
                    elapsed = time.monotonic() - start
                    rate = processed / elapsed if elapsed else 0
                    remaining = (len(jobs) - processed) / rate if rate else float("inf")
                    log.info(
                        "%d/%d processed (%d succeeded, %d failed) - %.2f tracks/s, ~%.0fs remaining",
                        processed,
                        len(jobs),
                        succeeded,
                        failed,
                        rate,
                        remaining,
                    )
        conn.commit()

    log.info(
        "Done: re-extracted features for %d/%d tracks (%d failed to load)",
        succeeded,
        len(jobs),
        failed,
    )


if __name__ == "__main__":
    main()
