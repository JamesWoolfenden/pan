from backend.db import (
    Track,
    connect,
    init_db,
    record_play_event,
    set_favorite,
    upsert_track,
)
from backend.stats import (
    favorites,
    most_played,
    most_skipped,
    never_played,
    recently_added,
    recently_played,
)


def _track(conn, title, file_path="Z:\\music\\x.mp3"):
    return upsert_track(
        conn,
        Track(
            external_id=title,
            source="local",
            title=title,
            artist="Artist",
            file_path=file_path,
        ),
    )


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
    unplayable = upsert_track(
        conn, Track(external_id="a", source="plex", title="No File", artist="Artist")
    )
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


def test_recently_played_orders_by_most_recent_play_and_dedupes_tracks():
    conn = connect(":memory:")
    init_db(conn)
    older = _track(conn, "Older")
    newer = _track(conn, "Newer")
    conn.commit()
    record_play_event(conn, older, 1.0)
    record_play_event(conn, newer, 1.0)
    record_play_event(conn, newer, 0.5)
    conn.commit()

    results = recently_played(conn)

    assert [r["id"] for r in results] == [newer, older]
    assert results[0]["play_count"] == 2


def test_recently_added_orders_by_insertion_newest_first():
    conn = connect(":memory:")
    init_db(conn)
    older = _track(conn, "Older")
    newer = _track(conn, "Newer")
    conn.commit()

    results = recently_added(conn)

    assert [r["id"] for r in results] == [newer, older]


def test_recently_added_order_unaffected_by_later_update():
    conn = connect(":memory:")
    init_db(conn)
    first = _track(conn, "First")
    second = _track(conn, "Second")
    conn.commit()
    upsert_track(
        conn,
        Track(
            external_id="First", source="local", title="First (fixed)", artist="Artist"
        ),
    )
    conn.commit()

    results = recently_added(conn)

    assert [r["id"] for r in results] == [second, first]


def test_favorites_lists_only_favorited_tracks_most_recent_first():
    conn = connect(":memory:")
    init_db(conn)
    liked_first = _track(conn, "Liked First")
    liked_second = _track(conn, "Liked Second")
    unliked = _track(conn, "Unliked")
    conn.commit()
    set_favorite(conn, liked_first, True)
    conn.commit()
    set_favorite(conn, liked_second, True)
    conn.commit()

    results = favorites(conn)

    ids = [r["id"] for r in results]
    assert ids == [liked_second, liked_first]
    assert unliked not in ids
    assert all(r["favorite"] for r in results)


def test_set_favorite_can_be_toggled_off():
    conn = connect(":memory:")
    init_db(conn)
    track = _track(conn, "Track")
    conn.commit()
    set_favorite(conn, track, True)
    conn.commit()
    set_favorite(conn, track, False)
    conn.commit()

    assert favorites(conn) == []
