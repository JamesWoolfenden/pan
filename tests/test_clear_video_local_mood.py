from backend.db import Track, connect, init_db, upsert_track
from scripts.clear_video_local_mood import clear


def test_clear_removes_mood_from_locally_tagged_video_rows():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn,
        Track(
            external_id="a",
            source="local_video",
            title="Dark City (1998)",
            genre="Sci-Fi",
            mood="driving",
            tag_source="local",
        ),
    )
    conn.commit()

    cleared = clear(conn)

    row = conn.execute(
        "SELECT mood, genre, tag_source FROM tracks WHERE id = ?", (track_id,)
    ).fetchone()
    assert cleared == 1
    assert row["mood"] is None
    assert row["genre"] == "Sci-Fi"  # untouched - this heuristic never set genre
    assert row["tag_source"] == "none"


def test_clear_leaves_cloud_tagged_video_mood_untouched():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn,
        Track(
            external_id="a",
            source="local_video",
            title="Dark City (1998)",
            mood="atmospheric",
            tag_source="cloud",
        ),
    )
    conn.commit()

    cleared = clear(conn)

    row = conn.execute("SELECT mood FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert cleared == 0
    assert row["mood"] == "atmospheric"


def test_clear_leaves_locally_tagged_audio_mood_untouched():
    # Same tag_source="local" heuristic, but a real song - tempo/energy-based mood is exactly
    # what it's for there, so only source="local_video" rows should ever be cleared.
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn,
        Track(
            external_id="a",
            source="local",
            title="Some Song",
            mood="driving",
            tag_source="local",
        ),
    )
    conn.commit()

    cleared = clear(conn)

    row = conn.execute("SELECT mood FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert cleared == 0
    assert row["mood"] == "driving"
