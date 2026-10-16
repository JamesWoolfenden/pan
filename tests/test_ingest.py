from backend.db import (
    Track,
    connect,
    init_db,
    merge_plex_metadata,
    set_ai_background,
    set_artist,
    set_audio_features,
    upsert_track,
)
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


def test_upsert_track_does_not_revert_a_manually_corrected_artist():
    """Regression test: a compilation track manually corrected via set_artist (e.g. unscrambling
    "Bacobens Rock Top 500", where every track's artist was wrongly set to the compiler) used to
    get silently reverted back to the source's own (wrong) metadata on the very next rescan,
    since upsert_track overwrote artist/title/album unconditionally."""
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="local:1", source="local", title="A", artist="Various Artists"))
    conn.commit()
    set_artist(conn, track_id, "The Rolling Stones")
    conn.commit()

    upsert_track(conn, Track(external_id="local:1", source="local", title="A", artist="Various Artists"))
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE external_id = 'local:1'").fetchone()
    assert row["artist"] == "The Rolling Stones"


def test_remap_plex_path_translates_nas_prefix_to_local_share(monkeypatch):
    monkeypatch.setattr("backend.ingest.plex_source.settings.plex_remote_music_path", "/volume1/plexdata/music")
    monkeypatch.setattr("backend.ingest.plex_source.settings.shared_drive_path", "Z:\\music")

    result = remap_plex_path("/volume1/plexdata/music/Artist/Album/01 - Song.flac")

    assert result == "Artist/Album/01 - Song.flac"


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


def test_merge_plex_metadata_does_not_revert_a_manually_corrected_artist():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn, Track(external_id="local:1", source="local", title="Song", artist="Various Artists",
                     file_path="Z:\\music\\a.flac")
    )
    conn.commit()
    set_artist(conn, track_id, "The Rolling Stones")
    conn.commit()

    plex_track = Track(
        external_id="plex:99", source="plex", plex_rating_key="99",
        title="Song", artist="Various Artists", album="Album", file_path="Z:\\music\\a.flac",
    )
    merge_plex_metadata(conn, track_id, plex_track)
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["artist"] == "The Rolling Stones"


def test_set_ai_background_persists_and_defaults_to_null():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="a", source="local", title="Song"))
    conn.commit()

    before = conn.execute("SELECT ai_background FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert before["ai_background"] is None

    set_ai_background(conn, track_id, "Some interesting trivia.")
    conn.commit()

    after = conn.execute("SELECT ai_background FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert after["ai_background"] == "Some interesting trivia."


def test_migrate_adds_ai_background_to_a_pre_existing_tracks_table():
    conn = connect(":memory:")
    # Simulate an older database created before ai_background existed (the real pre-migration
    # schema, minus that one column).
    conn.executescript("""
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL,
            plex_rating_key TEXT,
            file_path TEXT,
            title TEXT,
            artist TEXT,
            album TEXT,
            genre TEXT,
            mood TEXT,
            year INTEGER,
            duration_ms INTEGER,
            tempo REAL,
            energy REAL,
            musical_key TEXT,
            tag_source TEXT NOT NULL DEFAULT 'none'
        );
    """)
    conn.commit()

    init_db(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()}
    assert "ai_background" in columns


def test_migrate_adds_identity_source_defaulting_to_auto():
    conn = connect(":memory:")
    conn.executescript("""
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL,
            plex_rating_key TEXT,
            file_path TEXT,
            title TEXT,
            artist TEXT,
            album TEXT,
            genre TEXT,
            mood TEXT,
            year INTEGER,
            duration_ms INTEGER,
            tempo REAL,
            energy REAL,
            musical_key TEXT,
            tag_source TEXT NOT NULL DEFAULT 'none'
        );
    """)
    conn.execute("INSERT INTO tracks (external_id, source, title) VALUES ('local:1', 'local', 'Song')")
    conn.commit()

    init_db(conn)

    row = conn.execute("SELECT identity_source FROM tracks WHERE external_id = 'local:1'").fetchone()
    assert row["identity_source"] == "auto"
