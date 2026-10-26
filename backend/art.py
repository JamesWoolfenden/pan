from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import mutagen
from mutagen.mp4 import MP4Cover

from backend.paths import resolve as resolve_file_path

log = logging.getLogger(__name__)

_FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


def _extract_video_frame(path: Path) -> tuple[bytes, str] | None:
    """A frame grabbed partway into the file, the same way a video player's own scrubber
    thumbnail would - video containers essentially never carry a usable embedded cover picture
    mutagen can extract (Matroska/.mkv isn't supported by mutagen at all). Tries a point likely
    to be past any black/logo intro first, falling back to just past the start for anything
    shorter than that."""
    if not _FFMPEG_AVAILABLE:
        return None
    for seek_seconds in ("60", "2"):
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-ss",
                    seek_seconds,
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-f",
                    "image2",
                    "-c:v",
                    "mjpeg",
                    "-",
                ],
                capture_output=True,
                timeout=15,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if result.returncode == 0 and result.stdout:
            return result.stdout, "image/jpeg"
    return None


def extract_album_art(
    file_path: str, source: str | None = None
) -> tuple[bytes, str] | None:
    """Pull embedded cover art straight out of the audio file's own tags - FLAC's PICTURE
    block, an ID3 APIC frame (MP3), or an MP4 covr atom (M4A/AAC) - rather than maintaining a
    separate artwork cache/table, since the art already lives inside the file. Returns
    (image_bytes, mime_type), or None if the file can't be read or has no embedded art.
    `source` is passed through to resolve() so a local_video row can be found under
    VIDEO_DRIVE_PATH when it's not under SHARED_DRIVE_PATH - see that function's docstring."""
    path = resolve_file_path(file_path, source)
    if path is None or not path.exists():
        return None
    if source == "local_video":
        return _extract_video_frame(path)
    try:
        audio = mutagen.File(path)
    except Exception:
        log.debug("Failed to read audio tags for art: %s", path, exc_info=True)
        return None
    if audio is None:
        return None

    pictures = getattr(audio, "pictures", None)
    if pictures:
        pic = pictures[0]
        return pic.data, pic.mime or "image/jpeg"

    tags = getattr(audio, "tags", None)
    if tags is None:
        return None

    get_all = getattr(tags, "getall", None)
    if get_all:
        apics = get_all("APIC")
        if apics:
            return apics[0].data, apics[0].mime or "image/jpeg"

    covr = tags.get("covr") if hasattr(tags, "get") else None
    if covr:
        cover = covr[0]
        mime = "image/png" if cover.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
        return bytes(cover), mime

    return None
