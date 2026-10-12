from backend.db import EMBEDDING_DIM, Track, connect, find_similar_embeddings, init_db, upsert_embedding, upsert_track
from backend.search.cloud_query_parser import parse_query
from backend.search.engine import _base_title, _dedupe_by_song, _filtered_candidate_ids, album_tracks, similar, versions
from backend.search.query_router import is_complex_query


def test_is_complex_query_detects_negation():
    assert is_complex_query("upbeat 90s tracks but not metal")
    assert not is_complex_query("chill study music")


def test_parse_query_falls_back_without_api_key(monkeypatch):
    monkeypatch.setattr("backend.search.cloud_query_parser.settings.anthropic_api_key", "")
    result = parse_query("something upbeat")
    assert result == {"semantic_query": "something upbeat"}


def test_filtered_candidate_ids_applies_genre_exclude():
    conn = connect(":memory:")
    init_db(conn)
    keep_id = upsert_track(conn, Track(external_id="a", source="local", title="A", genre="Rock"))
    drop_id = upsert_track(conn, Track(external_id="b", source="local", title="B", genre="Metal"))
    conn.commit()

    ids = _filtered_candidate_ids(conn, {"genre_exclude": ["Metal"]})

    assert keep_id in ids
    assert drop_id not in ids


def test_filtered_candidate_ids_returns_none_without_hard_constraints():
    conn = connect(":memory:")
    init_db(conn)
    assert _filtered_candidate_ids(conn, {"semantic_query": "chill"}) is None


def test_dedupe_by_song_prefers_higher_quality_duplicate(tmp_path):
    mp3_path = tmp_path / "low.mp3"
    mp3_path.write_bytes(b"x" * 100)
    flac_path = tmp_path / "high.flac"
    flac_path.write_bytes(b"x" * 100)

    conn = connect(":memory:")
    init_db(conn)
    # The lower-quality mp3 ranks first (closer embedding match) but the dedupe should still
    # surface the flac copy, since it's the file you'd actually want to play.
    mp3_id = upsert_track(conn, Track(external_id="a", source="local", title="Song", artist="Artist", file_path=str(mp3_path)))
    flac_id = upsert_track(conn, Track(external_id="b", source="local", title="Song", artist="Artist", file_path=str(flac_path)))
    conn.commit()
    rows = [conn.execute("SELECT * FROM tracks WHERE id = ?", (mp3_id,)).fetchone(),
            conn.execute("SELECT * FROM tracks WHERE id = ?", (flac_id,)).fetchone()]

    result = _dedupe_by_song(rows, limit=25)

    assert len(result) == 1
    assert result[0]["id"] == flac_id


def test_dedupe_by_song_prefers_larger_file_within_same_format(tmp_path):
    small = tmp_path / "small.mp3"
    small.write_bytes(b"x" * 100)
    large = tmp_path / "large.mp3"
    large.write_bytes(b"x" * 1000)

    conn = connect(":memory:")
    init_db(conn)
    small_id = upsert_track(conn, Track(external_id="a", source="local", title="Song", artist="Artist", file_path=str(small)))
    large_id = upsert_track(conn, Track(external_id="b", source="local", title="Song", artist="Artist", file_path=str(large)))
    conn.commit()
    rows = [conn.execute("SELECT * FROM tracks WHERE id = ?", (small_id,)).fetchone(),
            conn.execute("SELECT * FROM tracks WHERE id = ?", (large_id,)).fetchone()]

    result = _dedupe_by_song(rows, limit=25)

    assert result[0]["id"] == large_id


def test_dedupe_by_song_leaves_distinct_songs_untouched():
    conn = connect(":memory:")
    init_db(conn)
    a = upsert_track(conn, Track(external_id="a", source="local", title="Song A", artist="Artist"))
    b = upsert_track(conn, Track(external_id="b", source="local", title="Song B", artist="Artist"))
    conn.commit()
    rows = [conn.execute("SELECT * FROM tracks WHERE id = ?", (a,)).fetchone(),
            conn.execute("SELECT * FROM tracks WHERE id = ?", (b,)).fetchone()]

    result = _dedupe_by_song(rows, limit=25)

    assert {row["id"] for row in result} == {a, b}


def test_find_similar_embeddings_ranks_closest_vector_first():
    conn = connect(":memory:")
    init_db(conn)
    target = upsert_track(conn, Track(external_id="t", source="local", title="Target"))
    close = upsert_track(conn, Track(external_id="c", source="local", title="Close"))
    far = upsert_track(conn, Track(external_id="f", source="local", title="Far"))
    conn.commit()

    base = [0.0] * EMBEDDING_DIM
    base[0] = 1.0
    close_vec = list(base)
    close_vec[1] = 0.1
    far_vec = [0.0] * EMBEDDING_DIM
    far_vec[-1] = 1.0

    upsert_embedding(conn, target, base)
    upsert_embedding(conn, close, close_vec)
    upsert_embedding(conn, far, far_vec)
    conn.commit()

    hits = find_similar_embeddings(conn, target, limit=10)
    ids_in_order = [tid for tid, _ in hits]

    assert ids_in_order[0] == target
    assert ids_in_order.index(close) < ids_in_order.index(far)


def test_similar_excludes_other_copies_of_the_anchor_song():
    conn = connect(":memory:")
    init_db(conn)
    anchor = upsert_track(conn, Track(external_id="a", source="local", title="Song", artist="Artist"))
    duplicate_copy = upsert_track(conn, Track(external_id="b", source="local", title="song", artist="artist"))
    other_song = upsert_track(conn, Track(external_id="c", source="local", title="Different", artist="Artist"))
    conn.commit()

    vec = [0.0] * EMBEDDING_DIM
    vec[0] = 1.0
    for tid in (anchor, duplicate_copy, other_song):
        upsert_embedding(conn, tid, vec)
    conn.commit()

    results = similar(conn, anchor, limit=10)

    ids = {r["id"] for r in results}
    assert duplicate_copy not in ids
    assert other_song in ids


def test_album_tracks_orders_by_leading_track_number_in_filename():
    conn = connect(":memory:")
    init_db(conn)
    anchor = upsert_track(conn, Track(
        external_id="a", source="local", title="Second", artist="Band", album="LP",
        file_path="Z:\\music\\Band\\LP\\02 Second.mp3",
    ))
    upsert_track(conn, Track(
        external_id="b", source="local", title="First", artist="Band", album="LP",
        file_path="Z:\\music\\Band\\LP\\01 First.mp3",
    ))
    upsert_track(conn, Track(
        external_id="c", source="local", title="Third", artist="Band", album="LP",
        file_path="Z:\\music\\Band\\LP\\03 Third.mp3",
    ))
    conn.commit()

    results = album_tracks(conn, anchor)

    assert [r["title"] for r in results] == ["First", "Second", "Third"]


def test_album_tracks_ignores_tracks_from_other_albums():
    conn = connect(":memory:")
    init_db(conn)
    anchor = upsert_track(conn, Track(external_id="a", source="local", title="A", artist="Band", album="LP"))
    upsert_track(conn, Track(external_id="b", source="local", title="B", artist="Band", album="Other LP"))
    conn.commit()

    results = album_tracks(conn, anchor)

    assert [r["title"] for r in results] == ["A"]


def test_album_tracks_prefers_best_quality_duplicate(tmp_path):
    mp3_path = tmp_path / "low.mp3"
    mp3_path.write_bytes(b"x" * 100)
    flac_path = tmp_path / "high.flac"
    flac_path.write_bytes(b"x" * 100)

    conn = connect(":memory:")
    init_db(conn)
    anchor = upsert_track(conn, Track(
        external_id="a", source="local", title="Song", artist="Band", album="LP", file_path=str(mp3_path),
    ))
    flac_id = upsert_track(conn, Track(
        external_id="b", source="local", title="Song", artist="Band", album="LP", file_path=str(flac_path),
    ))
    conn.commit()

    results = album_tracks(conn, anchor)

    assert len(results) == 1
    assert results[0]["id"] == flac_id


def test_base_title_strips_bracketed_and_dash_suffixes():
    assert _base_title("Sexy Boy [Radio Edit]") == "sexy boy"
    assert _base_title("Sexy Boy [Cassius Radio Mix]") == "sexy boy"
    assert _base_title("Cold Heart (PNAU Remix)") == "cold heart"
    assert _base_title("Song - Extended Mix") == "song"
    assert _base_title("Plain Title") == "plain title"


def test_versions_groups_remixes_of_the_same_song():
    conn = connect(":memory:")
    init_db(conn)
    anchor = upsert_track(conn, Track(external_id="a", source="local", title="Sexy Boy [Radio Edit]", artist="Air"))
    upsert_track(conn, Track(external_id="b", source="local", title="Sexy Boy [Cassius Radio Mix]", artist="Air"))
    upsert_track(conn, Track(external_id="c", source="local", title="Playground Love", artist="Air"))
    unrelated_artist = upsert_track(conn, Track(external_id="d", source="local", title="Sexy Boy [Remix]", artist="Someone Else"))
    conn.commit()

    results = versions(conn, anchor)

    titles = {r["title"] for r in results}
    assert titles == {"Sexy Boy [Radio Edit]", "Sexy Boy [Cassius Radio Mix]"}
    assert unrelated_artist not in {r["id"] for r in results}


def test_versions_dedupes_exact_duplicate_mixes_by_quality(tmp_path):
    mp3_path = tmp_path / "low.mp3"
    mp3_path.write_bytes(b"x" * 100)
    flac_path = tmp_path / "high.flac"
    flac_path.write_bytes(b"x" * 100)

    conn = connect(":memory:")
    init_db(conn)
    anchor = upsert_track(conn, Track(
        external_id="a", source="local", title="Song [Radio Edit]", artist="Artist", file_path=str(mp3_path),
    ))
    flac_id = upsert_track(conn, Track(
        external_id="b", source="local", title="Song [Radio Edit]", artist="Artist", file_path=str(flac_path),
    ))
    conn.commit()

    results = versions(conn, anchor)

    assert len(results) == 1
    assert results[0]["id"] == flac_id
