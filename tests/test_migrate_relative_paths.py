from backend.db import Track, connect, init_db, upsert_track
from scripts.migrate_relative_paths import migrate


def test_migrate_converts_absolute_paths_under_root():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn,
        Track(
            external_id="a",
            source="local",
            title="Song",
            file_path="Z:\\music\\Artist\\Song.flac",
        ),
    )
    conn.commit()

    counts = migrate(conn, "Z:\\music")
    conn.commit()

    row = conn.execute(
        "SELECT file_path FROM tracks WHERE id = ?", (track_id,)
    ).fetchone()
    assert row["file_path"] == "Artist/Song.flac"
    assert counts == {
        "converted": 1,
        "already_relative": 0,
        "skipped_not_under_root": 0,
    }


def test_migrate_leaves_already_relative_paths_untouched():
    conn = connect(":memory:")
    init_db(conn)
    upsert_track(
        conn,
        Track(
            external_id="a", source="local", title="Song", file_path="Artist/Song.flac"
        ),
    )
    conn.commit()

    counts = migrate(conn, "Z:\\music")

    assert counts == {
        "converted": 0,
        "already_relative": 1,
        "skipped_not_under_root": 0,
    }


def test_migrate_leaves_paths_outside_root_untouched():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn,
        Track(
            external_id="a",
            source="local",
            title="Song",
            file_path="D:\\elsewhere\\Song.flac",
        ),
    )
    conn.commit()

    counts = migrate(conn, "Z:\\music")

    row = conn.execute(
        "SELECT file_path FROM tracks WHERE id = ?", (track_id,)
    ).fetchone()
    assert row["file_path"] == "D:\\elsewhere\\Song.flac"
    assert counts == {
        "converted": 0,
        "already_relative": 0,
        "skipped_not_under_root": 1,
    }


def test_migrate_skips_tracks_with_no_file_path():
    conn = connect(":memory:")
    init_db(conn)
    upsert_track(conn, Track(external_id="a", source="local", title="Song"))
    conn.commit()

    counts = migrate(conn, "Z:\\music")

    assert counts == {
        "converted": 0,
        "already_relative": 0,
        "skipped_not_under_root": 0,
    }
