from backend.db import Track, connect, init_db, record_play_event, upsert_track
from backend.stats import most_played, most_skipped, never_played


def _track(conn, title, file_path="Z:\\music\\x.mp3"):
    return upsert_track(conn, Track(external_id=title, source="local", title=title, artist="Artist", file_path=file_path))


def test_most_played_orders_by_play_count_descending():
    conn = connect(":memory:")
    init_db(conn)
    popular = _track(conn, "Popular")
    rare = _track(conn, "Rare")
    conn.commit()
    for _ in range(3):
        record_play_event(conn, popular, 1.0)
    record_play_event(conn, rare, 1.0)
    conn.commit()

    results = most_played(conn)

    assert [r["id"] for r in results] == [popular, rare]
    assert results[0]["play_count"] == 3
    assert results[1]["play_count"] == 1


def test_never_played_excludes_tracks_with_play_events():
    conn = connect(":memory:")
    init_db(conn)
    played = _track(conn, "Played")
    unplayed = _track(conn, "Unplayed")
    conn.commit()
    record_play_event(conn, played, 1.0)
    conn.commit()

    results = never_played(conn)

    ids = [r["id"] for r in results]
    assert unplayed in ids
    assert played not in ids


def test_never_played_excludes_tracks_without_a_file_path():
    conn = connect(":memory:")
    init_db(conn)
    unplayable = upsert_track(conn, Track(external_id="a", source="plex", title="No File", artist="Artist"))
    conn.commit()

    results = never_played(conn)

    assert unplayable not in [r["id"] for r in results]


def test_most_skipped_requires_at_least_two_plays():
    conn = connect(":memory:")
    init_db(conn)
    once = _track(conn, "Played Once")
    conn.commit()
    record_play_event(conn, once, 0.05)
    conn.commit()

    results = most_skipped(conn)

    assert results == []


def test_most_skipped_orders_by_lowest_average_percent_listened():
    conn = connect(":memory:")
    init_db(conn)
    skipped = _track(conn, "Skipped")
    loved = _track(conn, "Loved")
    conn.commit()
    record_play_event(conn, skipped, 0.05)
    record_play_event(conn, skipped, 0.1)
    record_play_event(conn, loved, 0.95)
    record_play_event(conn, loved, 1.0)
    conn.commit()

    results = most_skipped(conn)

    assert [r["id"] for r in results] == [skipped, loved]
    assert results[0]["avg_percent_listened"] < results[1]["avg_percent_listened"]
