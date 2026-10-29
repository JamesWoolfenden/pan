import sys

import pytest

from backend.db import (
    Track,
    connect,
    create_playlist,
    delete_track,
    init_db,
    merge_plex_metadata,
    record_play_event,
    set_ai_background,
    set_artist,
    set_audio_features,
    upsert_embedding,
    upsert_track,
)
from backend.ingest.local_source import _position, normalize_path
from backend.ingest.merge import (
    backfill_local_track_positions,
    known_file_paths,
    path_to_id_map,
    prune_missing_local_tracks,
    prune_missing_plex_tracks,
)
from backend.ingest.plex_source import remap_plex_path

# See tests/test_paths.py's windows_only marker - normalize_path's separator-insensitivity
# relies on pathlib treating "\" as a path separator, which only Windows does.
windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises Windows-style path separator normalization",
)


@windows_only
def test_normalize_path_is_case_and_slash_insensitive():
    a = normalize_path("C:/Music/Song.mp3")
    b = normalize_path("c:\\Music\\Song.mp3")
    assert a == b


def test_known_file_paths_reflects_existing_tracks():
    conn = connect(":memory:")
    init_db(conn)

    upsert_track(
        conn,
        Track(
            external_id="plex:1", source="plex", file_path="C:/Music/a.mp3", title="A"
        ),
    )
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

    upsert_track(
        conn, Track(external_id="local:1", source="local", title="A", genre="Rock")
    )
    upsert_track(
        conn, Track(external_id="local:1", source="local", title="A", genre=None)
    )
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
    track_id = upsert_track(
        conn,
        Track(
            external_id="local:1", source="local", title="A", artist="Various Artists"
        ),
    )
    conn.commit()
    set_artist(conn, track_id, "The Rolling Stones")
    conn.commit()

    upsert_track(
        conn,
        Track(
            external_id="local:1", source="local", title="A", artist="Various Artists"
        ),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE external_id = 'local:1'").fetchone()
    assert row["artist"] == "The Rolling Stones"


def test_remap_plex_path_translates_nas_prefix_to_local_share(monkeypatch):
    monkeypatch.setattr(
        "backend.ingest.plex_source.settings.plex_remote_music_path",
        "/volume1/plexdata/music",
    )
    monkeypatch.setattr(
        "backend.ingest.plex_source.settings.shared_drive_path", "Z:\\music"
    )

    result = remap_plex_path("/volume1/plexdata/music/Artist/Album/01 - Song.flac")

    assert result == "Artist/Album/01 - Song.flac"


def test_remap_plex_path_leaves_unmatched_paths_untouched(monkeypatch):
    monkeypatch.setattr(
        "backend.ingest.plex_source.settings.plex_remote_music_path",
        "/volume1/plexdata/music",
    )
    monkeypatch.setattr(
        "backend.ingest.plex_source.settings.shared_drive_path", "Z:\\music"
    )

    result = remap_plex_path("/volume2/other/Artist/Song.flac")

    assert result == "/volume2/other/Artist/Song.flac"


@windows_only
def test_path_to_id_map_keys_by_normalized_path():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn,
        Track(external_id="local:1", source="local", file_path="Z:\\music\\a.flac"),
    )
    conn.commit()

    mapping = path_to_id_map(conn)

    assert mapping[normalize_path("Z:/music/a.flac")] == track_id


def test_merge_plex_metadata_preserves_id_and_audio_features():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn,
        Track(
            external_id="local:1",
            source="local",
            title="Song",
            file_path="Z:\\music\\a.flac",
        ),
    )
    set_audio_features(conn, track_id, tempo=120.0, energy=0.5, musical_key="C")
    conn.commit()

    plex_track = Track(
        external_id="plex:99",
        source="plex",
        plex_rating_key="99",
        title="Song",
        artist="Artist",
        album="Album",
        genre="Rock",
        year=1999,
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
        conn,
        Track(
            external_id="local:1",
            source="local",
            title="Song",
            artist="Various Artists",
            file_path="Z:\\music\\a.flac",
        ),
    )
    conn.commit()
    set_artist(conn, track_id, "The Rolling Stones")
    conn.commit()

    plex_track = Track(
        external_id="plex:99",
        source="plex",
        plex_rating_key="99",
        title="Song",
        artist="Various Artists",
        album="Album",
        file_path="Z:\\music\\a.flac",
    )
    merge_plex_metadata(conn, track_id, plex_track)
    conn.commit()

    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["artist"] == "The Rolling Stones"


def test_set_audio_features_persists_loudness_lufs_and_defaults_to_null():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="a", source="local", title="Song"))
    conn.commit()

    set_audio_features(conn, track_id, tempo=120.0, energy=0.5, musical_key="C")
    conn.commit()
    row = conn.execute(
        "SELECT loudness_lufs FROM tracks WHERE id = ?", (track_id,)
    ).fetchone()
    assert row["loudness_lufs"] is None

    set_audio_features(
        conn, track_id, tempo=120.0, energy=0.5, musical_key="C", loudness_lufs=-14.2
    )
    conn.commit()
    row = conn.execute(
        "SELECT loudness_lufs FROM tracks WHERE id = ?", (track_id,)
    ).fetchone()
    assert row["loudness_lufs"] == -14.2


def test_set_ai_background_persists_and_defaults_to_null():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(conn, Track(external_id="a", source="local", title="Song"))
    conn.commit()

    before = conn.execute(
        "SELECT ai_background FROM tracks WHERE id = ?", (track_id,)
    ).fetchone()
    assert before["ai_background"] is None

    set_ai_background(conn, track_id, "Some interesting trivia.")
    conn.commit()

    after = conn.execute(
        "SELECT ai_background FROM tracks WHERE id = ?", (track_id,)
    ).fetchone()
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

    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()
    }
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
    conn.execute(
        "INSERT INTO tracks (external_id, source, title) VALUES ('local:1', 'local', 'Song')"
    )
    conn.commit()

    init_db(conn)

    row = conn.execute(
        "SELECT identity_source FROM tracks WHERE external_id = 'local:1'"
    ).fetchone()
    assert row["identity_source"] == "auto"


def test_delete_track_cascades_to_embedding_history_and_playlists():
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn, Track(external_id="local:1", source="local", title="A")
    )
    other_id = upsert_track(
        conn, Track(external_id="local:2", source="local", title="B")
    )
    upsert_embedding(conn, track_id, [0.0] * 384)
    record_play_event(conn, track_id, 0.9)
    playlist_id = create_playlist(conn, "Mix", [track_id, other_id])
    conn.commit()

    delete_track(conn, track_id)
    conn.commit()

    assert (
        conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        is None
    )
    assert (
        conn.execute(
            "SELECT * FROM track_vectors WHERE rowid = ?", (track_id,)
        ).fetchone()
        is None
    )
    assert (
        conn.execute(
            "SELECT * FROM play_events WHERE track_id = ?", (track_id,)
        ).fetchone()
        is None
    )
    remaining = [
        row["track_id"]
        for row in conn.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,)
        ).fetchall()
    ]
    assert remaining == [other_id]


def test_prune_missing_plex_tracks_removes_tracks_not_seen():
    conn = connect(":memory:")
    init_db(conn)
    kept_ids = [
        upsert_track(
            conn,
            Track(
                external_id=f"plex:{i}",
                source="plex",
                plex_rating_key=str(i),
                title="Kept",
            ),
        )
        for i in range(9)
    ]
    stale_id = upsert_track(
        conn,
        Track(
            external_id="plex:stale",
            source="plex",
            plex_rating_key="stale",
            title="Gone",
        ),
    )
    conn.commit()

    # 1/10 missing is a plausible real removal, under the safety threshold.
    pruned = prune_missing_plex_tracks(
        conn, seen_rating_keys={str(i) for i in range(9)}
    )
    conn.commit()

    assert pruned == 1
    assert (
        conn.execute("SELECT * FROM tracks WHERE id = ?", (stale_id,)).fetchone()
        is None
    )
    for kept_id in kept_ids:
        assert (
            conn.execute("SELECT * FROM tracks WHERE id = ?", (kept_id,)).fetchone()
            is not None
        )


def test_prune_missing_plex_tracks_keeps_merged_rows_still_in_plex():
    conn = connect(":memory:")
    init_db(conn)
    # Cataloged by the local scan first, then enriched by Plex - keeps its "local:" id.
    merged_id = upsert_track(
        conn,
        Track(external_id="local:abc", source="local", title="Song", file_path="a.mp3"),
    )
    merge_plex_metadata(
        conn,
        merged_id,
        Track(external_id="plex:42", source="plex", plex_rating_key="42", title="Song"),
    )
    conn.commit()

    pruned = prune_missing_plex_tracks(conn, seen_rating_keys={"42"})

    assert pruned == 0
    assert (
        conn.execute("SELECT * FROM tracks WHERE id = ?", (merged_id,)).fetchone()
        is not None
    )


def test_prune_missing_plex_tracks_safety_threshold_blocks_mass_deletion():
    conn = connect(":memory:")
    init_db(conn)
    for i in range(10):
        upsert_track(
            conn,
            Track(
                external_id=f"plex:{i}",
                source="plex",
                plex_rating_key=str(i),
                title=f"Track {i}",
            ),
        )
    conn.commit()

    # Only one of ten tracks actually seen - looks like a bad/partial scan, not a real removal
    # of 90% of the library, so nothing should be deleted.
    pruned = prune_missing_plex_tracks(conn, seen_rating_keys={"0"})
    conn.commit()

    assert pruned == 0
    assert conn.execute("SELECT COUNT(*) c FROM tracks").fetchone()["c"] == 10


def test_prune_missing_local_tracks_removes_tracks_whose_file_is_gone(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "backend.ingest.merge.settings.shared_drive_path", str(tmp_path)
    )
    (tmp_path / "present.mp3").write_bytes(b"")
    conn = connect(":memory:")
    init_db(conn)
    kept_ids = [
        upsert_track(
            conn,
            Track(
                external_id=f"local:{i}",
                source="local",
                title="Kept",
                file_path="present.mp3",
            ),
        )
        for i in range(9)
    ]
    stale_id = upsert_track(
        conn,
        Track(
            external_id="local:stale",
            source="local",
            title="Gone",
            file_path="missing.mp3",
        ),
    )
    conn.commit()

    # 1/10 missing is a plausible real removal, under the safety threshold.
    pruned = prune_missing_local_tracks(conn)
    conn.commit()

    assert pruned == 1
    assert (
        conn.execute("SELECT * FROM tracks WHERE id = ?", (stale_id,)).fetchone()
        is None
    )
    for kept_id in kept_ids:
        assert (
            conn.execute("SELECT * FROM tracks WHERE id = ?", (kept_id,)).fetchone()
            is not None
        )


def test_position_parses_track_and_disc_number_tags():
    assert _position("3") == 3
    assert _position("03/12") == 3
    assert _position(" 2 / 2") == 2
    assert _position("") is None
    assert _position("0") is None
    assert _position("A1") is None


def test_backfill_local_track_positions_fills_only_rows_without_one(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "backend.ingest.merge.settings.shared_drive_path", str(tmp_path)
    )
    (tmp_path / "a.mp3").write_bytes(b"")
    (tmp_path / "untagged.mp3").write_bytes(b"")
    positions = {"a.mp3": (2, 5), "untagged.mp3": (None, None)}
    monkeypatch.setattr(
        "backend.ingest.merge.read_track_position", lambda path: positions[path.name]
    )
    conn = connect(":memory:")
    init_db(conn)
    needs_fill = upsert_track(
        conn, Track(external_id="local:a", source="local", file_path="a.mp3")
    )
    untagged = upsert_track(
        conn,
        Track(external_id="local:u", source="local", file_path="untagged.mp3"),
    )
    conn.commit()

    filled = backfill_local_track_positions(conn)

    assert filled == 1
    row = conn.execute(
        "SELECT disc_number, track_number FROM tracks WHERE id = ?", (needs_fill,)
    ).fetchone()
    assert (row["disc_number"], row["track_number"]) == (2, 5)
    assert (
        conn.execute(
            "SELECT track_number FROM tracks WHERE id = ?", (untagged,)
        ).fetchone()["track_number"]
        is None
    )


def test_prune_missing_local_tracks_skips_when_drive_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "backend.ingest.merge.settings.shared_drive_path",
        str(tmp_path / "does-not-exist"),
    )
    conn = connect(":memory:")
    init_db(conn)
    track_id = upsert_track(
        conn,
        Track(
            external_id="local:1", source="local", title="A", file_path="missing.mp3"
        ),
    )
    conn.commit()

    pruned = prune_missing_local_tracks(conn)
    conn.commit()

    assert pruned == 0
    assert (
        conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        is not None
    )
