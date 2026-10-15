from mutagen._vorbis import VCommentDict

from backend.db import Track, connect, init_db, set_lyrics, upsert_track
from backend.lyrics import _normalize_artist_name, extract_embedded_lyrics, fetch_online_lyrics, get_lyrics


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
    assert fetch_online_lyrics("", "Some Title") == (None, None, False)
    assert fetch_online_lyrics("Some Artist", "") == (None, None, False)


def test_fetch_online_lyrics_reports_transient_error_on_network_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr("backend.lyrics.urllib.request.urlopen", boom)

    assert fetch_online_lyrics("Some Artist", "Some Title") == (None, None, True)


def test_fetch_online_lyrics_reports_transient_error_on_rate_limit(monkeypatch):
    import urllib.error

    def boom(*args, **kwargs):
        raise urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("backend.lyrics.urllib.request.urlopen", boom)

    assert fetch_online_lyrics("Some Artist", "Some Title") == (None, None, True)


def test_fetch_online_lyrics_404_is_not_a_transient_error(monkeypatch):
    import urllib.error

    def boom(*args, **kwargs):
        raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    monkeypatch.setattr("backend.lyrics.urllib.request.urlopen", boom)

    assert fetch_online_lyrics("Some Artist", "Some Title") == (None, None, False)


def test_normalize_artist_name_moves_the_suffix_to_front():
    assert _normalize_artist_name("Beatles, The") == "The Beatles"
    assert _normalize_artist_name("Specials, The") == "The Specials"


def test_normalize_artist_name_returns_none_when_not_inverted():
    assert _normalize_artist_name("The Beatles") is None
    assert _normalize_artist_name("Radiohead") is None


def test_fetch_online_lyrics_retries_with_normalized_artist_after_404(monkeypatch):
    """Regression test: lrclib has "With a Little Help from My Friends" under "The Beatles",
    but this library's catalog stores the artist as the sort-friendly "Beatles, The" - a plain
    lookup 404s even though the song is right there under the normal name."""
    calls = []

    def fake_lrclib_get(artist, title, album, duration_ms):
        calls.append(artist)
        if artist == "The Beatles":
            return "lyrics text", None, False, False
        return None, None, False, True

    monkeypatch.setattr("backend.lyrics._lrclib_get", fake_lrclib_get)

    text, synced, transient_error = fetch_online_lyrics("Beatles, The", "Some Song")

    assert text == "lyrics text"
    assert transient_error is False
    assert calls == ["Beatles, The", "The Beatles"]


def test_fetch_online_lyrics_does_not_retry_when_artist_not_inverted(monkeypatch):
    calls = []

    def fake_lrclib_get(artist, title, album, duration_ms):
        calls.append(artist)
        return None, None, False, True

    monkeypatch.setattr("backend.lyrics._lrclib_get", fake_lrclib_get)

    text, synced, transient_error = fetch_online_lyrics("Radiohead", "Some Song")

    assert text is None
    assert calls == ["Radiohead"]


def test_fetch_online_lyrics_does_not_retry_on_transient_error(monkeypatch):
    calls = []

    def fake_lrclib_get(artist, title, album, duration_ms):
        calls.append(artist)
        return None, None, True, False

    monkeypatch.setattr("backend.lyrics._lrclib_get", fake_lrclib_get)

    text, synced, transient_error = fetch_online_lyrics("Beatles, The", "Some Song")

    assert transient_error is True
    assert calls == ["Beatles, The"]


def test_fetch_online_lyrics_returns_synced_lrc_alongside_plain_text(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("backend.lyrics.urllib.request.urlopen", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(
        "backend.lyrics.json.load", lambda resp: {"plainLyrics": "la la", "syncedLyrics": "[00:01.00] la la"}
    )

    text, synced, transient_error = fetch_online_lyrics("Some Artist", "Some Title")

    assert text == "la la"
    assert synced == "[00:01.00] la la"
    assert transient_error is False


def test_get_lyrics_falls_back_to_online_when_no_file_path(monkeypatch):
    monkeypatch.setattr("backend.lyrics.fetch_online_lyrics", lambda *a, **k: ("la la la", "[00:01.00] la la la", False))
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A", artist="B"))
    conn.commit()
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    text, source, synced = get_lyrics(row)

    assert text == "la la la"
    assert source == "online"
    assert synced == "[00:01.00] la la la"


def test_get_lyrics_reports_none_when_nothing_found(monkeypatch):
    monkeypatch.setattr("backend.lyrics.fetch_online_lyrics", lambda *a, **k: (None, None, False))
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A", artist="B"))
    conn.commit()
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    text, source, synced = get_lyrics(row)

    assert text is None
    assert source == "none"
    assert synced is None


def test_get_lyrics_reports_error_on_transient_failure_rather_than_none(monkeypatch):
    monkeypatch.setattr("backend.lyrics.fetch_online_lyrics", lambda *a, **k: (None, None, True))
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A", artist="B"))
    conn.commit()
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    text, source, synced = get_lyrics(row)

    assert text is None
    assert source == "error"
    assert synced is None


def test_get_lyrics_embedded_never_has_synced_timestamps(monkeypatch, tmp_path):
    path = tmp_path / "track.mp3"
    path.write_bytes(b"fake")
    monkeypatch.setattr("backend.lyrics.extract_embedded_lyrics", lambda fp: "embedded text")
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn, Track(external_id="local:1", source="local", title="A", artist="B", file_path=str(path))
    )
    conn.commit()
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    text, source, synced = get_lyrics(row)

    assert text == "embedded text"
    assert source == "embedded"
    assert synced is None


def test_set_lyrics_caches_empty_string_for_not_found():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A"))
    conn.commit()

    set_lyrics(conn, track_id, "")
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["lyrics"] == ""
    assert row["synced_lyrics"] is None


def test_set_lyrics_stores_synced_lrc_alongside_plain_text():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A"))
    conn.commit()

    set_lyrics(conn, track_id, "plain text", "[00:01.00] plain text")
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["lyrics"] == "plain text"
    assert row["synced_lyrics"] == "[00:01.00] plain text"
