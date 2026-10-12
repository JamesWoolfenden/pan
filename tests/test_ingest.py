from backend.db import Track, connect, init_db, merge_plex_metadata, set_audio_features, upsert_track
from backend.ingest.local_source import normalize_path
from backend.ingest.merge import known_file_paths, path_to_id_map
from backend.ingest.plex_source import remap_plex_path


def test_normalize_path_is_case_and_slash_insensitive():
    a = normalize_path("C:/Music/Song.mp3")
    b = normalize_path("c:\\Music\\Song.mp3")
    assert a == b


def test_known_file_paths_reflects_existing_tracks():
    conn = connect(":memory:")
    init_db(conn)

    upsert_track(conn, Track(external_id="plex:1", source="plex", file_path="C:/Music/a.mp3", title="A"))
    conn.commit()

    paths = known_file_paths(conn)
    assert normalize_path("C:/Music/a.mp3") in paths


def test_upsert_track_is_idempotent_on_external_id():
    conn = connect(":memory:")
    init_db(conn)

    upsert_track(conn, Track(external_id="local:1", source="local", title="Original"))
    upsert_track(conn, Track(external_id="local:1", source="local", title="Updated"))
    conn.commit()

    rows = conn.execute("SELECT * FROM tracks WHERE external_id = 'local:1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == "Updated"


def test_upsert_track_does_not_clobber_existing_genre_with_null():
    conn = connect(":memory:")
    init_db(conn)

    upsert_track(conn, Track(external_id="local:1", source="local", title="A", genre="Rock"))
    upsert_track(conn, Track(external_id="local:1", source="local", title="A", genre=None))
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE external_id = 'local:1'").fetchone()
    assert row["genre"] == "Rock"


def test_remap_plex_path_translates_nas_prefix_to_local_share(monkeypatch):
    monkeypatch.setattr("backend.ingest.plex_source.settings.plex_remote_music_path", "/volume1/plexdata/music")
    monkeypatch.setattr("backend.ingest.plex_source.settings.shared_drive_path", "Z:\\music")

    result = remap_plex_path("/volume1/plexdata/music/Artist/Album/01 - Song.flac")

    assert result == "Z:\\music\\Artist\\Album\\01 - Song.flac"


def test_remap_plex_path_leaves_unmatched_paths_untouched(monkeypatch):
    monkeypatch.setattr("backend.ingest.plex_source.settings.plex_remote_music_path", "/volume1/plexdata/music")
    monkeypatch.setattr("backend.ingest.plex_source.settings.shared_drive_path", "Z:\\music")

    result = remap_plex_path("/volume2/other/Artist/Song.flac")

    assert result == "/volume2/other/Artist/Song.flac"


def test_path_to_id_map_keys_by_normalized_path():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", file_path="Z:\\music\\a.flac"))
    conn.commit()

    mapping = path_to_id_map(conn)

    assert mapping[normalize_path("Z:/music/a.flac")] == track_id


def test_merge_plex_metadata_preserves_id_and_audio_features():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn, Track(external_id="local:1", source="local", title="Song", file_path="Z:\\music\\a.flac")
    )
    set_audio_features(conn, track_id, tempo=120.0, energy=0.5, musical_key="C")
    conn.commit()

    plex_track = Track(
        external_id="plex:99", source="plex", plex_rating_key="99",
        title="Song", artist="Artist", album="Album", genre="Rock", year=1999,
        file_path="Z:\\music\\a.flac",
    )
    merge_plex_metadata(conn, track_id, plex_track)
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["id"] == track_id
    assert row["plex_rating_key"] == "99"
    assert row["genre"] == "Rock"
    assert row["tempo"] == 120.0
    assert conn.execute("SELECT COUNT(*) c FROM tracks").fetchone()["c"] == 1
