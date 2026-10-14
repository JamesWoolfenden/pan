from backend.art import extract_album_art


def test_extract_album_art_returns_none_for_missing_file():
    assert extract_album_art("Z:\\music\\does-not-exist.mp3") is None


def test_extract_album_art_returns_none_for_non_audio_file(tmp_path):
    path = tmp_path / "not-audio.mp3"
    path.write_text("this is not an audio file")

    assert extract_album_art(str(path)) is None
