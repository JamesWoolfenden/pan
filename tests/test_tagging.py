from backend.db import Track, connect, init_db, set_tags_list, tags_list, upsert_track
from backend.tagging.cloud_tagger import suggest_tags
from backend.tagging.local_tagger import apply_local_tags, infer_mood


def test_infer_mood_slow_quiet_is_mellow():
    assert infer_mood(tempo=70, energy=0.2) == "mellow"


def test_infer_mood_fast_loud_is_energetic():
    assert infer_mood(tempo=160, energy=0.9) == "energetic"


def test_apply_local_tags_fills_missing_mood_from_features():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A", genre="Rock"))
    conn.commit()

    resolved = apply_local_tags(conn, track_id, genre="Rock", mood=None, tempo=160, energy=0.9)
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert resolved is True
    assert row["mood"] == "energetic"
    assert row["tag_source"] == "local"


def test_apply_local_tags_reports_unresolved_when_genre_and_features_missing():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A"))
    conn.commit()

    resolved = apply_local_tags(conn, track_id, genre=None, mood=None, tempo=None, energy=None)

    assert resolved is False


def test_set_tags_list_normalizes_case_whitespace_and_duplicates():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A"))
    conn.commit()

    set_tags_list(conn, track_id, [" Road Trip ", "ROAD TRIP", "live"])
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert tags_list(row) == ["live", "road trip"]


def test_set_tags_list_empty_clears_tags():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A"))
    conn.commit()
    set_tags_list(conn, track_id, ["live"])
    conn.commit()

    set_tags_list(conn, track_id, [])
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert tags_list(row) == []


def test_suggest_tags_falls_back_without_api_key(monkeypatch):
    monkeypatch.setattr("backend.tagging.cloud_tagger.settings.anthropic_api_key", "")
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="I Need a Man", artist="Eurythmics"))
    conn.commit()
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    result = suggest_tags(row)

    assert result == {"genres": [], "moods": []}
