from backend.db import Track, connect, init_db, set_audio_features, upsert_track
from backend.search.embeddings import track_text


def _row(conn, **kwargs):
    track_id = upsert_track(conn, Track(external_id="a", source="local", **kwargs))
    conn.commit()
    return conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()


def test_track_text_excludes_title():
    conn = connect(":memory:")
    init_db(conn)
    row = _row(conn, title="Can You Feel It", artist="Mr. Fingers", album="Chicago Trax", genre="House", mood="upbeat")

    text = track_text(row)

    assert "Can You Feel It" not in text
    assert "Mr. Fingers" in text
    assert "House" in text


def test_track_text_repeats_genre_for_weight():
    conn = connect(":memory:")
    init_db(conn)
    row = _row(conn, title="X", artist="Artist", genre="Deep House")

    text = track_text(row)

    assert text.count("Deep House") == 2


def test_track_text_does_not_include_tempo_as_text():
    # Tempo is used as a precise numeric factor in engine.py's similarity ranking instead -
    # folding it into the text embedding too would double-count it.
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="a", source="local", title="X", artist="Artist"))
    conn.commit()
    set_audio_features(conn, track_id, tempo=125.0, energy=0.8, musical_key="E")
    conn.commit()
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    text = track_text(row)

    assert "tempo" not in text


def test_track_text_includes_tags():
    from backend.db import set_tags_list

    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="a", source="local", title="X", artist="Artist"))
    conn.commit()
    set_tags_list(conn, track_id, ["road trip", "live"])
    conn.commit()
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    text = track_text(row)

    assert "road trip" in text
    assert "live" in text


def test_track_text_handles_missing_fields_gracefully():
    conn = connect(":memory:")
    init_db(conn)
    row = _row(conn, title="X", artist="Artist")

    text = track_text(row)

    assert text == "Artist"
