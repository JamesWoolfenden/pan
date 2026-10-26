from backend.db import (
    Track,
    connect,
    init_db,
    set_album,
    set_artist,
    set_tags_list,
    set_title,
    tags_list,
    tracks_missing_identity,
    upsert_track,
)
from backend.tagging.cloud_tagger import (
    PROMPT,
    VIDEO_PROMPT,
    suggest_tags,
    tag_with_cloud,
)
from backend.tagging.local_tagger import apply_local_tags, infer_mood


def test_infer_mood_slow_quiet_is_mellow():
    assert infer_mood(tempo=70, energy=0.2) == "mellow"


def test_infer_mood_fast_loud_is_energetic():
    assert infer_mood(tempo=160, energy=0.9) == "energetic"


def test_apply_local_tags_fills_missing_mood_from_features():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn, Track(external_id="local:1", source="local", title="A", genre="Rock")
    )
    conn.commit()

    resolved = apply_local_tags(
        conn, track_id, genre="Rock", mood=None, tempo=160, energy=0.9
    )
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert resolved is True
    assert row["mood"] == "energetic"
    assert row["tag_source"] == "local"


def test_apply_local_tags_reports_unresolved_when_genre_and_features_missing():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn, Track(external_id="local:1", source="local", title="A")
    )
    conn.commit()

    resolved = apply_local_tags(
        conn, track_id, genre=None, mood=None, tempo=None, energy=None
    )

    assert resolved is False


def test_set_tags_list_normalizes_case_whitespace_and_duplicates():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn, Track(external_id="local:1", source="local", title="A")
    )
    conn.commit()

    set_tags_list(conn, track_id, [" Road Trip ", "ROAD TRIP", "live"])
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert tags_list(row) == ["live", "road trip"]


def test_set_tags_list_empty_clears_tags():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn, Track(external_id="local:1", source="local", title="A")
    )
    conn.commit()
    set_tags_list(conn, track_id, ["live"])
    conn.commit()

    set_tags_list(conn, track_id, [])
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert tags_list(row) == []


def test_set_artist_corrects_a_mislabeled_artist():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn,
        Track(
            external_id="local:1", source="local", title="A", artist="Various Artists"
        ),
    )
    conn.commit()

    set_artist(conn, track_id, "The Specials")
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["artist"] == "The Specials"


def test_set_title_corrects_a_mislabeled_title():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn,
        Track(external_id="local:1", source="local", title="01 track", artist="Artist"),
    )
    conn.commit()

    set_title(conn, track_id, "Ghost Town")
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["title"] == "Ghost Town"


def test_set_album_corrects_a_mislabeled_album():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn,
        Track(
            external_id="local:1",
            source="local",
            title="Satisfaction",
            artist="The Rolling Stones",
            album="Bacobens Rock Top 500",
        ),
    )
    conn.commit()

    set_album(conn, track_id, "Out of Our Heads")
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["album"] == "Out of Our Heads"


def test_tracks_missing_identity_flags_blank_title_or_artist():
    conn = connect(":memory:")
    init_db(conn)
    good = upsert_track(
        conn,
        Track(external_id="local:1", source="local", title="A Song", artist="A Band"),
    )
    no_title = upsert_track(
        conn, Track(external_id="local:2", source="local", title="", artist="A Band")
    )
    no_artist = upsert_track(
        conn, Track(external_id="local:3", source="local", title="A Song", artist="")
    )
    conn.commit()

    flagged = {row["id"] for row in tracks_missing_identity(conn)}

    assert flagged == {no_title, no_artist}
    assert good not in flagged


def test_tag_with_cloud_splits_video_and_music_rows_with_different_prompts(monkeypatch):
    # Movies/TV episodes shouldn't be tagged with the music prompt (asks about artist/album,
    # invites a tempo-style mood like "driving") - see VIDEO_PROMPT's own docstring for why.
    monkeypatch.setattr(
        "backend.tagging.cloud_tagger.settings.anthropic_api_key", "fake-key"
    )
    monkeypatch.setattr("backend.tagging.cloud_tagger._client", lambda: object())

    conn = connect(":memory:")
    init_db(conn)
    song_id = upsert_track(conn, Track(external_id="a", source="local", title="Song"))
    video_id = upsert_track(
        conn, Track(external_id="b", source="local_video", title="Movie")
    )
    conn.commit()
    rows = conn.execute("SELECT * FROM tracks").fetchall()

    calls = []

    def fake_tag_batch(client, batch, prompt_template, is_video):
        calls.append((is_video, [row["id"] for row in batch], prompt_template))
        return [
            {"id": row["id"], "genre": "TestGenre", "mood": "TestMood"} for row in batch
        ]

    monkeypatch.setattr("backend.tagging.cloud_tagger._tag_batch", fake_tag_batch)

    tagged = tag_with_cloud(conn, rows)
    conn.commit()

    assert tagged == 2
    assert len(calls) == 2
    by_is_video = {c[0]: c for c in calls}
    assert by_is_video[False][1] == [song_id]
    assert by_is_video[False][2] is PROMPT
    assert by_is_video[True][1] == [video_id]
    assert by_is_video[True][2] is VIDEO_PROMPT

    row = conn.execute(
        "SELECT genre, mood FROM tracks WHERE id = ?", (video_id,)
    ).fetchone()
    assert row["genre"] == "TestGenre"
    assert row["mood"] == "TestMood"


def test_suggest_tags_falls_back_without_api_key(monkeypatch):
    monkeypatch.setattr("backend.tagging.cloud_tagger.settings.anthropic_api_key", "")
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn,
        Track(
            external_id="local:1",
            source="local",
            title="I Need a Man",
            artist="Eurythmics",
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    result = suggest_tags(row)

    assert result == {"genres": [], "moods": []}
