from backend import lastfm, scrobble
from backend.db import Track, connect, get_track, init_db, upsert_track


def _enable(mocker):
    mocker.patch.object(lastfm.settings, "lastfm_api_key", "key")
    mocker.patch.object(lastfm.settings, "lastfm_api_secret", "secret")
    mocker.patch.object(lastfm.settings, "lastfm_session_key", "sk")


def _conn_with_track(duration_ms=200_000, artist="Artist", title="Title"):
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn,
        Track(
            external_id="t",
            source="local",
            title=title,
            artist=artist,
            album="Album",
            file_path="Z:\\music\\x.mp3",
            duration_ms=duration_ms,
        ),
    )
    conn.commit()
    return conn, get_track(conn, track_id)


def test_is_scrobblable_needs_half_the_track():
    assert scrobble.is_scrobblable(200_000, 0.5)
    assert not scrobble.is_scrobblable(200_000, 0.49)


def test_is_scrobblable_caps_requirement_at_four_minutes():
    # A 20-minute track qualifies after 4 minutes (20% of it), not 10.
    assert scrobble.is_scrobblable(1_200_000, 0.21)
    assert not scrobble.is_scrobblable(1_200_000, 0.19)


def test_is_scrobblable_rejects_short_or_unknown_duration():
    assert not scrobble.is_scrobblable(30_000, 1.0)
    assert not scrobble.is_scrobblable(None, 1.0)


def test_enqueue_does_nothing_when_not_configured(mocker):
    mocker.patch.object(lastfm.settings, "lastfm_session_key", "")
    conn, track = _conn_with_track()
    assert not scrobble.enqueue_play(conn, track, 1.0)
    assert conn.execute("SELECT COUNT(*) FROM scrobble_queue").fetchone()[0] == 0


def test_enqueue_queues_qualifying_play_with_start_timestamp(mocker):
    _enable(mocker)
    mocker.patch.object(scrobble.time, "time", return_value=10_000.0)
    conn, track = _conn_with_track(duration_ms=200_000)

    assert scrobble.enqueue_play(conn, track, 0.75)

    row = conn.execute("SELECT * FROM scrobble_queue").fetchone()
    assert (row["artist"], row["track"], row["album"]) == ("Artist", "Title", "Album")
    assert row["duration_s"] == 200
    assert row["timestamp"] == 10_000 - 150  # 75% of 200s listened


def test_enqueue_skips_early_skips(mocker):
    _enable(mocker)
    conn, track = _conn_with_track()
    assert not scrobble.enqueue_play(conn, track, 0.1)


def test_sign_matches_lastfm_algorithm(mocker):
    import hashlib

    _enable(mocker)
    sig = lastfm._sign({"method": "m", "api_key": "k", "format": "json", "b": "2"})
    # format is excluded; params sorted by name; secret appended.
    assert sig == hashlib.md5(b"api_keykb2methodmsecret").hexdigest()


def test_scrobble_builds_indexed_batch_params(mocker):
    _enable(mocker)
    post = mocker.patch.object(lastfm, "_post_signed", return_value={"scrobbles": {}})
    ok = lastfm.scrobble(
        [
            {
                "artist": "A",
                "track": "T",
                "album": "Al",
                "duration": 100,
                "timestamp": 5,
            },
            {"artist": "B", "track": "U", "timestamp": 6},
        ]
    )
    assert ok
    params = post.call_args.args[0]
    assert params["method"] == "track.scrobble"
    assert params["sk"] == "sk"
    assert params["artist[0]"] == "A" and params["album[0]"] == "Al"
    assert params["track[1]"] == "U" and "album[1]" not in params


def test_scrobble_retries_on_network_error_and_temporary_api_error(mocker):
    _enable(mocker)
    entries = [{"artist": "A", "track": "T", "timestamp": 1}]
    mocker.patch.object(lastfm, "_post_signed", side_effect=OSError("down"))
    assert lastfm.scrobble(entries) is False
    mocker.patch.object(
        lastfm, "_post_signed", return_value={"error": 11, "message": "x"}
    )
    assert lastfm.scrobble(entries) is False


def test_scrobble_drops_unrecoverable_api_error(mocker):
    _enable(mocker)
    mocker.patch.object(
        lastfm, "_post_signed", return_value={"error": 6, "message": "bad"}
    )
    assert lastfm.scrobble([{"artist": "A", "track": "T", "timestamp": 1}]) is True


def test_flush_deletes_accepted_and_keeps_failed(mocker, tmp_path):
    _enable(mocker)
    db_file = tmp_path / "pan.db"
    mocker.patch("backend.config.settings.db_path", str(db_file))
    conn = connect(db_file)
    init_db(conn)
    conn.execute(
        "INSERT INTO scrobble_queue (artist, track, timestamp) VALUES ('A', 'T', 1)"
    )
    conn.commit()
    conn.close()

    mocker.patch.object(lastfm, "scrobble", return_value=False)
    scrobble.flush_queue()
    conn = connect(db_file)
    assert conn.execute("SELECT COUNT(*) FROM scrobble_queue").fetchone()[0] == 1
    conn.close()

    mocker.patch.object(lastfm, "scrobble", return_value=True)
    scrobble.flush_queue()
    conn = connect(db_file)
    assert conn.execute("SELECT COUNT(*) FROM scrobble_queue").fetchone()[0] == 0
