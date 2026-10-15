from mutagen._vorbis import VCommentDict

from backend.db import Track, connect, init_db, set_lyrics, upsert_track
from backend.lyrics import extract_embedded_lyrics, fetch_online_lyrics, get_lyrics


def test_extract_embedded_lyrics_returns_none_for_missing_file():
    assert extract_embedded_lyrics("Z:\\music\\does-not-exist.mp3") is None


def test_extract_embedded_lyrics_returns_none_for_non_audio_file(tmp_path):
    path = tmp_path / "not-audio.mp3"
    path.write_text("this is not an audio file")

    assert extract_embedded_lyrics(str(path)) is None


def test_extract_embedded_lyrics_handles_flac_with_no_lyrics_tag(tmp_path, monkeypatch):
    """Regression test: VCommentDict (FLAC/OGG) raises ValueError, not KeyError, for a key it
    considers invalid - which the MP4-only "\xa9lyr" atom name is, as far as Vorbis comments are
    concerned. Checking that key against a real FLAC file with no embedded lyrics used to crash
    this with an unhandled ValueError instead of just reporting no lyrics found."""
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake")
    fake_audio = type("FakeAudio", (), {"tags": VCommentDict(), "pictures": []})()
    monkeypatch.setattr("backend.lyrics.mutagen.File", lambda p: fake_audio)

    assert extract_embedded_lyrics(str(path)) is None


def test_fetch_online_lyrics_returns_none_without_artist_or_title():
    assert fetch_online_lyrics("", "Some Title") == (None, False)
    assert fetch_online_lyrics("Some Artist", "") == (None, False)


def test_fetch_online_lyrics_reports_transient_error_on_network_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr("backend.lyrics.urllib.request.urlopen", boom)

    assert fetch_online_lyrics("Some Artist", "Some Title") == (None, True)


def test_fetch_online_lyrics_reports_transient_error_on_rate_limit(monkeypatch):
    import urllib.error

    def boom(*args, **kwargs):
        raise urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("backend.lyrics.urllib.request.urlopen", boom)

    assert fetch_online_lyrics("Some Artist", "Some Title") == (None, True)


def test_fetch_online_lyrics_404_is_not_a_transient_error(monkeypatch):
    import urllib.error

    def boom(*args, **kwargs):
        raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    monkeypatch.setattr("backend.lyrics.urllib.request.urlopen", boom)

    assert fetch_online_lyrics("Some Artist", "Some Title") == (None, False)


def test_get_lyrics_falls_back_to_online_when_no_file_path(monkeypatch):
    monkeypatch.setattr("backend.lyrics.fetch_online_lyrics", lambda *a, **k: ("la la la", False))
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A", artist="B"))
    conn.commit()
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    text, source = get_lyrics(row)

    assert text == "la la la"
    assert source == "online"


def test_get_lyrics_reports_none_when_nothing_found(monkeypatch):
    monkeypatch.setattr("backend.lyrics.fetch_online_lyrics", lambda *a, **k: (None, False))
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A", artist="B"))
    conn.commit()
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    text, source = get_lyrics(row)

    assert text is None
    assert source == "none"


def test_get_lyrics_reports_error_on_transient_failure_rather_than_none(monkeypatch):
    monkeypatch.setattr("backend.lyrics.fetch_online_lyrics", lambda *a, **k: (None, True))
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A", artist="B"))
    conn.commit()
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    text, source = get_lyrics(row)

    assert text is None
    assert source == "error"


def test_set_lyrics_caches_empty_string_for_not_found():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A"))
    conn.commit()

    set_lyrics(conn, track_id, "")
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["lyrics"] == ""
