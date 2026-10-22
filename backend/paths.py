from __future__ import annotations

import re
from pathlib import Path

from backend.config import settings

# A path is "already absolute" (and should be used as-is, not joined to the music root) if it
# has a Windows drive letter, is a UNC path, or starts with a POSIX root - checked by pattern
# rather than Path.is_absolute(), since that's flavor-dependent (PureWindowsPath treats a
# leading "/" with no drive as *not* absolute, which would wrongly get it joined to the root).
_ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|//|/)")


def is_absolute(path: str) -> bool:
    return bool(_ABSOLUTE_RE.match(path))


def resolve(file_path: str | None, source: str | None = None) -> Path | None:
    """Resolve a stored file_path to a path usable for I/O on this machine. New rows store
    paths relative to SHARED_DRIVE_PATH so the catalog is portable across machines with
    different roots (e.g. this Windows box vs. the NAS it might later run on) - but rows
    written before this existed have absolute paths, so handle both.

    A local_video row's file_path can be relative to EITHER root: SHARED_DRIVE_PATH (video just
    living in a subfolder of the regular library - the common case) or VIDEO_DRIVE_PATH (a
    genuinely separate drive/share for video, when configured). Rather than recording at ingest
    time which root a given file came from (a schema change for what's a rare split-library
    case), just try SHARED_DRIVE_PATH first and fall back to VIDEO_DRIVE_PATH only if that
    doesn't exist - one extra stat() call, only for video rows, only when the primary root
    doesn't have it."""
    if not file_path:
        return None
    if is_absolute(file_path):
        return Path(file_path)
    primary = Path(settings.shared_drive_path) / file_path
    if source == "local_video" and settings.video_drive_path and not primary.exists():
        return Path(settings.video_drive_path) / file_path
    return primary


def to_relative(absolute_path: str, root: str) -> str:
    """Store a path relative to root when possible (POSIX-separated, so it's portable to a
    Linux/NAS host too), falling back to the absolute path if it isn't actually under root."""
    try:
        return Path(absolute_path).relative_to(root).as_posix()
    except ValueError:
        return absolute_path
