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


def resolve(file_path: str | None) -> Path | None:
    """Resolve a stored file_path to a path usable for I/O on this machine. New rows store
    paths relative to SHARED_DRIVE_PATH so the catalog is portable across machines with
    different roots (e.g. this Windows box vs. the NAS it might later run on) - but rows
    written before this existed have absolute paths, so handle both."""
    if not file_path:
        return None
    if is_absolute(file_path):
        return Path(file_path)
    return Path(settings.shared_drive_path) / file_path


def to_relative(absolute_path: str, root: str) -> str:
    """Store a path relative to root when possible (POSIX-separated, so it's portable to a
    Linux/NAS host too), falling back to the absolute path if it isn't actually under root."""
    try:
        return Path(absolute_path).relative_to(root).as_posix()
    except ValueError:
        return absolute_path
