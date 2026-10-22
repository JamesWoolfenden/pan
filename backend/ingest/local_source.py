from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import mutagen

from backend.db import Track
from backend.paths import resolve, to_relative

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".aac", ".wma"}
# Concert/music-video files, ingested from the same shared-drive walk as audio. mutagen has no
# Matroska/WebM module at all (confirmed by inspecting the installed package), so these go through
# ffprobe (_read_video_tags below) instead of mutagen for every extension here, not just .mkv -
# one code path, and ffprobe reads every container's tag/format structure more consistently than
# mutagen's per-format modules do anyway.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv"}

_FFPROBE_AVAILABLE = shutil.which("ffprobe") is not None
if not _FFPROBE_AVAILABLE:
    log.warning(
        "ffprobe not found on PATH - video files will be ingested with filename-only metadata "
        "(no duration/tags) until ffmpeg is installed"
    )


def normalize_path(path: str, source: str | None = None) -> str:
    """Normalize a filesystem path for cross-source matching (case/separator-insensitive).
    Accepts either an absolute path, or one stored relative to SHARED_DRIVE_PATH or (for
    local_video rows, when `source` is passed) VIDEO_DRIVE_PATH - see resolve()'s docstring.
    `source` only matters for a relative path read back from the DB; during an active walk the
    path is already absolute (resolve() short-circuits on that), so callers here that process
    paths fresh off disk don't need to pass it."""
    resolved = resolve(path, source)
    return str(resolved.resolve()).lower() if resolved else ""


def _stable_id(path: str) -> str:
    return hashlib.sha1(normalize_path(path).encode("utf-8")).hexdigest()


def iter_local_tracks(
    root: str,
    skip_paths: set[str] | None = None,
    extensions: set[str] | None = None,
) -> Iterator[Track]:
    """Walk `root` for audio and video files and yield a Track per file, skipping any path
    already known (e.g. because Plex already reports it) via its normalized form in
    `skip_paths`. Video files (concert/music-video rips) live in the same walk as audio by
    default - they naturally share library folders - and come out with source='local_video'
    instead of 'local', tagged via ffprobe (_read_video_tags) instead of mutagen (_read_tags).

    `extensions` restricts which files this walk considers (default: AUDIO_EXTENSIONS |
    VIDEO_EXTENSIONS, i.e. everything) - used to scan VIDEO_DRIVE_PATH, when configured, for
    video only, since that root's whole purpose is holding video the main library walk
    (SHARED_DRIVE_PATH) wouldn't otherwise reach."""
    skip_paths = skip_paths or set()
    allowed_extensions = (
        extensions if extensions is not None else AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
    )
    root_path = Path(root)
    if not root_path.exists():
        log.warning("Shared drive path does not exist: %s", root)
        return

    for file_path in root_path.rglob("*"):
        suffix = file_path.suffix.lower()
        is_video = suffix in VIDEO_EXTENSIONS
        if suffix not in allowed_extensions or not file_path.is_file():
            continue

        normalized = normalize_path(str(file_path))
        if normalized in skip_paths:
            continue

        tags = _read_video_tags(file_path) if is_video else _read_tags(file_path)
        yield Track(
            external_id=f"local:{_stable_id(str(file_path))}",
            source="local_video" if is_video else "local",
            file_path=to_relative(str(file_path), root),
            title=tags.get("title", file_path.stem),
            artist=tags.get("artist", ""),
            album=tags.get("album", ""),
            genre=tags.get("genre"),
            year=tags.get("year"),
            duration_ms=tags.get("duration_ms"),
            tag_source="metadata" if tags.get("genre") else "none",
        )


def _read_tags(file_path: Path) -> dict:
    try:
        audio = mutagen.File(file_path, easy=True)
    except Exception:
        log.debug("Failed to read tags for %s", file_path, exc_info=True)
        return {}
    if audio is None:
        return {}

    def first(key: str) -> str | None:
        values = audio.get(key)
        return values[0] if values else None

    year_raw = first("date") or first("year")
    year = None
    if year_raw:
        try:
            year = int(str(year_raw)[:4])
        except ValueError:
            year = None

    duration_ms = (
        int(audio.info.length * 1000) if getattr(audio, "info", None) else None
    )

    return {
        "title": first("title"),
        "artist": first("artist"),
        "album": first("album"),
        "genre": first("genre"),
        "year": year,
        "duration_ms": duration_ms,
    }


def _read_video_tags(file_path: Path) -> dict:
    """ffprobe-based metadata for video containers - mutagen has no Matroska/WebM support at
    all, and ffprobe handles every container's tag/format structure more consistently than
    mutagen's per-format modules, so this is used for all video extensions, not just .mkv.
    Returns {} on any failure (ffprobe missing, corrupt file, timeout) - enrichment, never
    something that should break an ingest scan, same philosophy as backend/lastfm.py."""
    if not _FFPROBE_AVAILABLE:
        return {}
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        fmt = json.loads(result.stdout).get("format", {})
        tags = {k.lower(): v for k, v in fmt.get("tags", {}).items()}
        duration_s = fmt.get("duration")
        return {
            "duration_ms": int(float(duration_s) * 1000) if duration_s else None,
            "title": tags.get("title"),
            "artist": tags.get("artist"),
            "album": tags.get("album"),
            "genre": tags.get("genre"),
        }
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError, ValueError):
        log.warning(
            "ffprobe failed for %s - ingesting with filename-only metadata", file_path
        )
        return {}
