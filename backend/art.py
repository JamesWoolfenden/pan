from __future__ import annotations

import logging

import mutagen
from mutagen.mp4 import MP4Cover

from backend.paths import resolve as resolve_file_path

log = logging.getLogger(__name__)


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
