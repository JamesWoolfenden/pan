import sys

import pytest

from backend.paths import is_absolute, resolve, to_relative

# pathlib's Path is platform-dependent (WindowsPath vs PosixPath), so joining/splitting a
# Windows-style absolute path (backslashes, drive letter) only behaves as asserted here when
# actually running on Windows - on Linux, backslashes aren't a path separator at all, so these
# would fail for reasons that have nothing to do with a real bug (a "Z:\..." path was never
# going to resolve to a real file on Linux either way, deployment-wise - see README).
windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises Windows-style absolute path joining/splitting",
)


def test_is_absolute_recognizes_windows_drive_path():
    assert is_absolute("Z:\\music\\Song.flac")
    assert is_absolute("C:/music/Song.flac")


def test_is_absolute_recognizes_unc_and_posix_paths():
    assert is_absolute("\\\\nas\\share\\Song.flac")
    assert is_absolute("/volume1/music/Song.flac")


def test_is_absolute_rejects_relative_path():
    assert not is_absolute("Artist/Album/Song.flac")


@windows_only
def test_resolve_joins_relative_path_to_shared_drive_path(monkeypatch):
    monkeypatch.setattr("backend.paths.settings.shared_drive_path", "Z:\\music")
    result = resolve("Artist/Album/Song.flac")
    assert str(result) == "Z:\\music\\Artist\\Album\\Song.flac"


def test_resolve_leaves_absolute_path_untouched(monkeypatch):
    monkeypatch.setattr("backend.paths.settings.shared_drive_path", "Z:\\music")
    result = resolve("C:\\Windows\\Media\\chime.wav")
    assert str(result) == "C:\\Windows\\Media\\chime.wav"


def test_resolve_returns_none_for_empty_path():
    assert resolve(None) is None
    assert resolve("") is None


@windows_only
def test_to_relative_strips_root_and_uses_posix_separators():
    result = to_relative("Z:\\music\\Artist\\Album\\Song.flac", "Z:\\music")
    assert result == "Artist/Album/Song.flac"


def test_to_relative_falls_back_to_absolute_when_not_under_root():
    result = to_relative("D:\\other\\Song.flac", "Z:\\music")
    assert result == "D:\\other\\Song.flac"
