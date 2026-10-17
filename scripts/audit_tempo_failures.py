"""Find genuinely unreadable audio files in the library - a report to act on (delete, replace,
or ignore), not an auto-delete.

Earlier versions of this script tried to infer failures retroactively from tempo values left
over in the DB. Both approaches turned out unreliable: a "shared tempo value" collision count
just rediscovers that real songs cluster around common BPMs (120, 128, ...), and exact-matching
against the old hop_length=512 formula's outputs doesn't work either, since hop_length=128 is
exactly hop_length=512/4 - so 1-in-4 of the new, correctly-computed values land on an old-grid
value too, purely by construction, not because extraction failed. There is no way to tell "this
track failed" from its current tempo value alone.

So this does a direct, ground-truth test instead: try to decode a short snippet of every track
with a file_path (parallelized - this is pure CPU/IO work with no shared state, same as
scripts/reextract_tempo.py). Only 1 second of audio, no beat-tracking - enough to catch "Illegal
Audio-MPEG-Header"/decode failures without the slow part. For each failure, also checks whether
another catalogued copy of the same song exists and is actually readable - if so, the bad file
is a redundant duplicate (safe to just delete); if not, it's the only copy (needs replacing -
re-ripping/re-downloading - not just deleting, or the song is lost)."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ProcessPoolExecutor

import librosa

from backend.db import connection, init_db
from backend.paths import resolve as resolve_file_path
from backend.search.engine import _song_key

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("audit_tempo_failures")

WORKERS = 10
LOG_EVERY = 500


def _check(job: tuple[int, str]) -> tuple[int, dict]:
    track_id, file_path_str = job
    path = resolve_file_path(file_path_str)
    if path is None:
        return track_id, {"status": "no_resolvable_path"}
    if not path.exists():
        return track_id, {"status": "missing_file", "path": str(path)}
    try:
        size = path.stat().st_size
    except OSError as exc:
        return track_id, {"status": "stat_error", "path": str(path), "error": str(exc)}
    if size == 0:
        return track_id, {"status": "zero_length", "path": str(path)}

    try:
        y, _sr = librosa.load(path, sr=22050, mono=True, duration=1)
    except Exception as exc:
        return track_id, {
            "status": "decode_error",
            "path": str(path),
            "size": size,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if y.size == 0:
        return track_id, {
            "status": "empty_decoded_audio",
            "path": str(path),
            "size": size,
        }

    return track_id, {"status": "ok", "path": str(path), "size": size}


def main() -> None:
    with connection() as conn:
        init_db(conn)
        rows = conn.execute(
            "SELECT id, title, artist, file_path FROM tracks WHERE file_path IS NOT NULL"
        ).fetchall()
        by_id = {row["id"]: row for row in rows}
        song_index: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            path = resolve_file_path(row["file_path"])
            readable = path is not None and path.exists() and path.stat().st_size > 0
            song_index.setdefault(_song_key(row), []).append(
                {"id": row["id"], "file_path": row["file_path"], "readable": readable}
            )

    jobs = [(row["id"], row["file_path"]) for row in rows]
    log.info("Checking %d tracks across %d workers", len(jobs), WORKERS)

    failures = []
    start = time.monotonic()
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        for processed, (track_id, diagnosis) in enumerate(
            pool.map(_check, jobs, chunksize=8), start=1
        ):
            if diagnosis["status"] != "ok":
                row = by_id[track_id]
                duplicates = [
                    d
                    for d in song_index.get(_song_key(row), [])
                    if d["id"] != track_id and d["readable"]
                ]
                failures.append(
                    {
                        "id": track_id,
                        "title": row["title"],
                        "artist": row["artist"],
                        "file_path": row["file_path"],
                        **diagnosis,
                        "duplicate_exists": bool(duplicates),
                        "duplicate_candidates": duplicates,
                        "recommendation": "safe to delete - readable duplicate exists"
                        if duplicates
                        else "only copy - replace (re-rip/re-download) rather than delete, or the song is lost",
                    }
                )
            if processed % LOG_EVERY == 0 or processed == len(jobs):
                elapsed = time.monotonic() - start
                rate = processed / elapsed if elapsed else 0
                remaining = (len(jobs) - processed) / rate if rate else float("inf")
                log.info(
                    "%d/%d checked (%d failures so far) - %.1f tracks/s, ~%.0fs remaining",
                    processed,
                    len(jobs),
                    len(failures),
                    rate,
                    remaining,
                )

    out_path = "scripts/tempo_failure_audit.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(failures, f, indent=2)

    by_status: dict[str, int] = {}
    for r in failures:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    log.info(
        "Done: %d failures out of %d tracks. By status: %s",
        len(failures),
        len(jobs),
        by_status,
    )
    log.info("Full report written to %s", out_path)


if __name__ == "__main__":
    main()
