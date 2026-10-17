from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Iterator

import mutagen

from backend.db import Track
from backend.paths import resolve, to_relative

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".aac", ".wma"}


def normalize_path(path: str) -> str:
    """Normalize a filesystem path for cross-source matching (case/separator-insensitive).
    Accepts either an absolute path or one stored relative to SHARED_DRIVE_PATH."""
    resolved = resolve(path)
    return str(resolved.resolve()).lower() if resolved else ""


def _stable_id(path: str) -> str:
    return hashlib.sha1(normalize_path(path).encode("utf-8")).hexdigest()


def iter_local_tracks(root: str, skip_paths: set[str] | None = None) -> Iterator[Track]:
    """Walk `root` for audio files and yield a Track per file, skipping any path already
    known (e.g. because Plex already reports it) via its normalized form in `skip_paths`."""
    skip_paths = skip_paths or set()
    root_path = Path(root)
    if not root_path.exists():
        log.warning("Shared drive path does not exist: %s", root)
        return

    for file_path in root_path.rglob("*"):
        if file_path.suffix.lower() not in AUDIO_EXTENSIONS or not file_path.is_file():
            continue

        normalized = normalize_path(str(file_path))
        if normalized in skip_paths:
            continue

        tags = _read_tags(file_path)
        yield Track(
            external_id=f"local:{_stable_id(str(file_path))}",
            source="local",
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
