from backend.db import Track, connect, init_db, set_lyrics, upsert_track
from backend.lyrics import extract_embedded_lyrics, fetch_online_lyrics, get_lyrics


def test_extract_embedded_lyrics_returns_none_for_missing_file():
    assert extract_embedded_lyrics("Z:\\music\\does-not-exist.mp3") is None


def test_extract_embedded_lyrics_returns_none_for_non_audio_file(tmp_path):
    path = tmp_path / "not-audio.mp3"
    path.write_text("this is not an audio file")

    assert extract_embedded_lyrics(str(path)) is None


def test_fetch_online_lyrics_returns_none_without_artist_or_title():
    assert fetch_online_lyrics("", "Some Title") is None
    assert fetch_online_lyrics("Some Artist", "") is None


def test_fetch_online_lyrics_returns_none_on_network_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr("backend.lyrics.urllib.request.urlopen", boom)

    assert fetch_online_lyrics("Some Artist", "Some Title") is None


def test_get_lyrics_falls_back_to_online_when_no_file_path(monkeypatch):
    monkeypatch.setattr("backend.lyrics.fetch_online_lyrics", lambda *a, **k: "la la la")
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A", artist="B"))
    conn.commit()
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    text, source = get_lyrics(row)

    assert text == "la la la"
    assert source == "online"


def test_get_lyrics_reports_none_when_nothing_found(monkeypatch):
    monkeypatch.setattr("backend.lyrics.fetch_online_lyrics", lambda *a, **k: None)
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A", artist="B"))
    conn.commit()
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    text, source = get_lyrics(row)

    assert text is None
    assert source == "none"


def test_set_lyrics_caches_empty_string_for_not_found():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A"))
    conn.commit()

    set_lyrics(conn, track_id, "")
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["lyrics"] == ""
