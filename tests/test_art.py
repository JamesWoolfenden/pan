import shutil
import subprocess

import pytest

from backend.art import extract_album_art

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH"
)


def test_extract_album_art_returns_none_for_missing_file():
    assert extract_album_art("Z:\\music\\does-not-exist.mp3") is None


def test_extract_album_art_returns_none_for_non_audio_file(tmp_path):
    path = tmp_path / "not-audio.mp3"
    path.write_text("this is not an audio file")

    assert extract_album_art(str(path)) is None


def test_extract_album_art_returns_none_for_missing_video_file():
    assert (
        extract_album_art("Z:\\video\\does-not-exist.mp4", source="local_video") is None
    )


@ffmpeg_required
def test_extract_album_art_grabs_a_frame_for_local_video(tmp_path):
    # A synthetic 3s clip (no real media needed) - long enough that the 2s fallback seek lands
    # inside it even though the primary 60s seek doesn't.
    path = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=64x64:rate=1",
            str(path),
        ],
        capture_output=True,
        timeout=30,
        check=True,
    )

    art = extract_album_art(str(path), source="local_video")

    assert art is not None
    data, mime = art
    assert mime == "image/jpeg"
    assert len(data) > 0
