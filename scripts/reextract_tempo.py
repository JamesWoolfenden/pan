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

A file that crashes the decoder (a corrupt MP3 can kill the worker process outright) is
logged and skipped instead of aborting the run.

The default is 2 workers, safe on a NAS CPU alongside the running server. Local video rows are
skipped: tempo/energy/key are music-specific heuristics (see reindex.py).
"""

from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from backend.db import connection, init_db, set_audio_features
from backend.paths import resolve as resolve_file_path
from backend.tagging.audio_features import extract_features

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("reextract_tempo")

BATCH_SIZE = 50  # also the commit interval
LOG_EVERY = 200
DEFAULT_WORKERS = 2


def _extract(job: tuple[int, str]) -> tuple[int, dict | None]:
    track_id, file_path = job
    try:
        return track_id, extract_features(resolve_file_path(file_path))
    except Exception:  # one bad file must not abort a library-wide pass.
        log.exception(
            "Feature extraction raised for track %d (%s)", track_id, file_path
        )
        return track_id, None


def _isolate(batch: list[tuple[int, str]]) -> list[tuple[int, dict | None]]:
    """Re-run a batch whose pool broke one job at a time, each in a fresh single-worker pool,
    so the file that actually kills its worker is identified and skipped rather than taking
    the rest of the batch (and the whole run) down with it."""
    results = []
    for job in batch:
        try:
            with ProcessPoolExecutor(max_workers=1) as pool:
                results.append(pool.submit(_extract, job).result())
        except BrokenProcessPool:
            log.error("Decoder crashed on track %d (%s) - skipping", job[0], job[1])
            results.append((job[0], None))
    return results


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
    processed = 0
    start = time.monotonic()

    pool = ProcessPoolExecutor(max_workers=workers)
    try:
        with connection() as conn:
            for offset in range(0, len(jobs), BATCH_SIZE):
                batch = jobs[offset : offset + BATCH_SIZE]
                try:
                    results = list(pool.map(_extract, batch))
                except BrokenProcessPool:
                    log.warning(
                        "A worker died in a batch of %d - retrying one at a time to find the culprit",
                        len(batch),
                    )
                    pool.shutdown(wait=False, cancel_futures=True)
                    results = _isolate(batch)
                    pool = ProcessPoolExecutor(max_workers=workers)

                for track_id, features in results:
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
                conn.commit()

                previous, processed = processed, processed + len(batch)
                if processed // LOG_EVERY > previous // LOG_EVERY or processed == len(
                    jobs
                ):
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
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    log.info(
        "Done: re-extracted features for %d/%d tracks (%d failed to load or crashed)",
        succeeded,
        len(jobs),
        failed,
    )


if __name__ == "__main__":
    main()
