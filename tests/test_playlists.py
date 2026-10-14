from backend.db import (
    Track,
    add_track_to_playlist,
    connect,
    create_playlist,
    delete_playlist,
    get_playlist,
    get_playlist_track_ids,
    init_db,
    list_playlists,
    rename_playlist,
    set_playlist_tracks,
    upsert_track,
)
from backend.search.engine import playlist_tracks


def _make_tracks(conn, n):
    return [
        upsert_track(conn, Track(external_id=f"t{i}", source="local", title=f"Song {i}", artist="Artist"))
        for i in range(n)
    ]


def test_create_playlist_preserves_track_order():
    conn = connect(":memory:")
    init_db(conn)
    ids = _make_tracks(conn, 3)
    conn.commit()

    playlist_id = create_playlist(conn, "My Mix", [ids[2], ids[0], ids[1]])
    conn.commit()

    assert get_playlist_track_ids(conn, playlist_id) == [ids[2], ids[0], ids[1]]


def test_playlist_tracks_returns_full_track_dicts_in_order():
    conn = connect(":memory:")
    init_db(conn)
    ids = _make_tracks(conn, 2)
    conn.commit()
    playlist_id = create_playlist(conn, "My Mix", [ids[1], ids[0]])
    conn.commit()

    results = playlist_tracks(conn, playlist_id)

    assert [r["id"] for r in results] == [ids[1], ids[0]]
    assert results[0]["title"] == "Song 1"


def test_list_playlists_includes_track_count():
    conn = connect(":memory:")
    init_db(conn)
    ids = _make_tracks(conn, 3)
    conn.commit()
    create_playlist(conn, "Full", ids)
    create_playlist(conn, "Empty", [])
    conn.commit()

    playlists = {row["name"]: row["track_count"] for row in list_playlists(conn)}

    assert playlists == {"Full": 3, "Empty": 0}


def test_rename_playlist():
    conn = connect(":memory:")
    init_db(conn)
    playlist_id = create_playlist(conn, "Old Name", [])
    conn.commit()

    rename_playlist(conn, playlist_id, "New Name")
    conn.commit()

    assert get_playlist(conn, playlist_id)["name"] == "New Name"


def test_delete_playlist_removes_playlist_and_its_tracks():
    conn = connect(":memory:")
    init_db(conn)
    ids = _make_tracks(conn, 2)
    conn.commit()
    playlist_id = create_playlist(conn, "Temp", ids)
    conn.commit()

    delete_playlist(conn, playlist_id)
    conn.commit()

    assert get_playlist(conn, playlist_id) is None
    assert get_playlist_track_ids(conn, playlist_id) == []


def test_add_track_to_playlist_appends_after_existing_tracks():
    conn = connect(":memory:")
    init_db(conn)
    ids = _make_tracks(conn, 3)
    conn.commit()
    playlist_id = create_playlist(conn, "Mix", ids[:2])
    conn.commit()

    add_track_to_playlist(conn, playlist_id, ids[2])
    conn.commit()

    assert get_playlist_track_ids(conn, playlist_id) == ids


def test_add_track_to_playlist_works_on_an_initially_empty_playlist():
    conn = connect(":memory:")
    init_db(conn)
    ids = _make_tracks(conn, 1)
    conn.commit()
    playlist_id = create_playlist(conn, "Empty", [])
    conn.commit()

    add_track_to_playlist(conn, playlist_id, ids[0])
    conn.commit()

    assert get_playlist_track_ids(conn, playlist_id) == ids


def test_set_playlist_tracks_replaces_the_full_list_and_order():
    conn = connect(":memory:")
    init_db(conn)
    ids = _make_tracks(conn, 4)
    conn.commit()
    playlist_id = create_playlist(conn, "Mix", ids[:2])
    conn.commit()

    set_playlist_tracks(conn, playlist_id, [ids[3], ids[1]])
    conn.commit()

    assert get_playlist_track_ids(conn, playlist_id) == [ids[3], ids[1]]


def test_set_playlist_tracks_can_empty_a_playlist():
    conn = connect(":memory:")
    init_db(conn)
    ids = _make_tracks(conn, 2)
    conn.commit()
    playlist_id = create_playlist(conn, "Mix", ids)
    conn.commit()

    set_playlist_tracks(conn, playlist_id, [])
    conn.commit()

    assert get_playlist_track_ids(conn, playlist_id) == []
