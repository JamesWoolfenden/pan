from backend.db import (
    EMBEDDING_DIM,
    Track,
    connect,
    create_playlist,
    find_similar_embeddings,
    init_db,
    set_tags_list,
    upsert_embedding,
    upsert_track,
)
from backend.search.cloud_query_parser import parse_query
from backend.search.engine import (
    _apply_genre_aliases,
    _base_title,
    _dedupe_by_song,
    _filtered_candidate_ids,
    _key_distance,
    _numeric_distance,
    album_tracks,
    discography,
    search,
    search_by_tag,
    similar,
    suggest_for_playlist,
    versions,
)
from backend.search.query_router import is_complex_query


def test_is_complex_query_detects_negation():
    assert is_complex_query("upbeat 90s tracks but not metal")
    assert not is_complex_query("chill study music")


def test_is_complex_query_detects_bare_decade_mentions():
    # Without the "from the" prefix - this used to skip the year filter entirely.
    assert is_complex_query("1990s house classics")
    assert is_complex_query("90s house")
    assert is_complex_query("2000s pop")


def test_parse_query_falls_back_without_api_key(monkeypatch):
    monkeypatch.setattr(
        "backend.search.cloud_query_parser.settings.anthropic_api_key", ""
    )
    result = parse_query("something upbeat")
    assert result == {"semantic_query": "something upbeat"}


def test_filtered_candidate_ids_applies_genre_exclude():
    conn = connect(":memory:")
    init_db(conn)
    keep_id = upsert_track(
        conn, Track(external_id="a", source="local", title="A", genre="Rock")
    )
    drop_id = upsert_track(
        conn, Track(external_id="b", source="local", title="B", genre="Metal")
    )
    conn.commit()

    ids = _filtered_candidate_ids(conn, {"genre_exclude": ["Metal"]})

    assert keep_id in ids
    assert drop_id not in ids


def test_filtered_candidate_ids_returns_none_without_hard_constraints():
    conn = connect(":memory:")
    init_db(conn)
    assert _filtered_candidate_ids(conn, {"semantic_query": "chill"}) is None


def test_filtered_candidate_ids_applies_media_type_video():
    # "video" is a small candidate set (few local_video rows in a real library), so it's worth
    # a hard filter/exact-rank pass. "audio" is the opposite - excluding only that same small
    # minority - and is handled as a post-filter in _search_with_filter instead (see
    # test_search_media_type_audio_excludes_video_tracks), not here.
    conn = connect(":memory:")
    init_db(conn)
    upsert_track(conn, Track(external_id="a", source="local", title="A"))
    upsert_track(conn, Track(external_id="b", source="plex", title="B"))
    video_id = upsert_track(
        conn, Track(external_id="c", source="local_video", title="C")
    )
    conn.commit()

    assert _filtered_candidate_ids(conn, {"media_type": "video"}) == {video_id}
    assert _filtered_candidate_ids(conn, {"media_type": "audio"}) is None


def test_search_media_type_audio_excludes_video_tracks(monkeypatch):
    conn = connect(":memory:")
    init_db(conn)
    audio_id = upsert_track(conn, Track(external_id="a", source="local", title="A"))
    video_id = upsert_track(
        conn, Track(external_id="b", source="local_video", title="B")
    )
    conn.commit()

    query_vec = [0.0] * EMBEDDING_DIM
    query_vec[0] = 1.0
    upsert_embedding(conn, audio_id, list(query_vec))
    upsert_embedding(conn, video_id, list(query_vec))

    monkeypatch.setattr("backend.search.engine.embed_text", lambda q: query_vec)

    results = search(conn, "whatever", limit=10, media_type="audio")

    ids = [r["id"] for r in results]
    assert audio_id in ids
    assert video_id not in ids


def test_filtered_candidate_ids_year_range_excludes_null_year_without_content_filter():
    # A pure decade query (no genre/mood) should stay strict - letting every undated track
    # through would defeat the point of the year filter.
    conn = connect(":memory:")
    init_db(conn)
    dated_id = upsert_track(
        conn, Track(external_id="a", source="local", title="A", year=1985)
    )
    undated_id = upsert_track(conn, Track(external_id="b", source="local", title="B"))
    conn.commit()

    ids = _filtered_candidate_ids(conn, {"year_min": 1980, "year_max": 1989})

    assert dated_id in ids
    assert undated_id not in ids


def test_filtered_candidate_ids_year_range_includes_null_year_alongside_genre_filter():
    # Ghosttown by The Specials has no year tagged - "1980s ska" should still surface it since
    # genre already did real narrowing, so letting an undated-but-genre-matched track through is
    # safer than silently dropping a genuine match for missing metadata.
    conn = connect(":memory:")
    init_db(conn)
    dated_id = upsert_track(
        conn, Track(external_id="a", source="local", title="A", genre="Ska", year=1981)
    )
    undated_id = upsert_track(
        conn, Track(external_id="b", source="local", title="B", genre="Ska")
    )
    wrong_genre_id = upsert_track(
        conn, Track(external_id="c", source="local", title="C", genre="Rock")
    )
    conn.commit()

    ids = _filtered_candidate_ids(
        conn, {"genre_include": ["Ska"], "year_min": 1980, "year_max": 1989}
    )

    assert dated_id in ids
    assert undated_id in ids
    assert wrong_genre_id not in ids


def test_apply_genre_aliases_maps_two_tone_to_ska():
    for query in ["two tone", "2 tone", "2-tone", "Two-Tone revival"]:
        filt = _apply_genre_aliases(query, {"semantic_query": query})
        assert filt["genre_include"] == ["ska"]


def test_filtered_candidate_ids_tag_include_matches_exact_token_not_substring():
    # "rock" should not match a track tagged "baroque" - tags are comma-joined and matched as
    # whole tokens, not a plain substring.
    conn = connect(":memory:")
    init_db(conn)
    from backend.db import set_tags_list

    rock_id = upsert_track(conn, Track(external_id="a", source="local", title="A"))
    baroque_id = upsert_track(conn, Track(external_id="b", source="local", title="B"))
    conn.commit()
    set_tags_list(conn, rock_id, ["rock", "road trip"])
    set_tags_list(conn, baroque_id, ["baroque"])
    conn.commit()

    ids = _filtered_candidate_ids(conn, {"tag_include": ["rock"]})

    assert rock_id in ids
    assert baroque_id not in ids


def test_apply_genre_aliases_leaves_unrelated_queries_untouched():
    filt = _apply_genre_aliases(
        "chill study music", {"semantic_query": "chill study music"}
    )
    assert "genre_include" not in filt


def test_dedupe_by_song_supports_pagination_via_offset_plus_limit_then_slice():
    # This is the pattern search() uses for offset-based pagination: dedupe down to
    # (offset + limit) entries, then slice [offset:] for the requested page.
    conn = connect(":memory:")
    init_db(conn)
    for i in range(5):
        upsert_track(
            conn,
            Track(
                external_id=str(i), source="local", title=f"Song {i}", artist="Artist"
            ),
        )
    conn.commit()
    rows = [
        conn.execute("SELECT * FROM tracks WHERE title = ?", (f"Song {i}",)).fetchone()
        for i in range(5)
    ]

    page1 = _dedupe_by_song(rows, limit=2)
    page2 = _dedupe_by_song(rows, limit=4)[2:]

    assert [r["title"] for r in page1] == ["Song 0", "Song 1"]
    assert [r["title"] for r in page2] == ["Song 2", "Song 3"]


def test_dedupe_by_song_merges_plex_reversed_article_artist_with_local_copy(tmp_path):
    # Plex tags this artist "Specials, The" (its sort-name convention); the local file tags it
    # "The Specials". Without normalizing, these looked like two different artists and both
    # copies of every track showed up in search - including the Plex one, which has no file_path
    # and so isn't even playable.
    mp3_path = tmp_path / "ghosttown.mp3"
    mp3_path.write_bytes(b"x" * 100)

    conn = connect(":memory:")
    init_db(conn)
    plex_id = upsert_track(
        conn,
        Track(
            external_id="a", source="plex", title="Ghosttown", artist="Specials, The"
        ),
    )
    local_id = upsert_track(
        conn,
        Track(
            external_id="b",
            source="local",
            title="Ghosttown",
            artist="The Specials",
            file_path=str(mp3_path),
        ),
    )
    conn.commit()
    rows = [
        conn.execute("SELECT * FROM tracks WHERE id = ?", (plex_id,)).fetchone(),
        conn.execute("SELECT * FROM tracks WHERE id = ?", (local_id,)).fetchone(),
    ]

    result = _dedupe_by_song(rows, limit=25)

    assert len(result) == 1
    assert result[0]["id"] == local_id


def test_dedupe_by_song_prefers_higher_quality_duplicate(tmp_path):
    mp3_path = tmp_path / "low.mp3"
    mp3_path.write_bytes(b"x" * 100)
    flac_path = tmp_path / "high.flac"
    flac_path.write_bytes(b"x" * 100)

    conn = connect(":memory:")
    init_db(conn)
    # The lower-quality mp3 ranks first (closer embedding match) but the dedupe should still
    # surface the flac copy, since it's the file you'd actually want to play.
    mp3_id = upsert_track(
        conn,
        Track(
            external_id="a",
            source="local",
            title="Song",
            artist="Artist",
            file_path=str(mp3_path),
        ),
    )
    flac_id = upsert_track(
        conn,
        Track(
            external_id="b",
            source="local",
            title="Song",
            artist="Artist",
            file_path=str(flac_path),
        ),
    )
    conn.commit()
    rows = [
        conn.execute("SELECT * FROM tracks WHERE id = ?", (mp3_id,)).fetchone(),
        conn.execute("SELECT * FROM tracks WHERE id = ?", (flac_id,)).fetchone(),
    ]

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
    small_id = upsert_track(
        conn,
        Track(
            external_id="a",
            source="local",
            title="Song",
            artist="Artist",
            file_path=str(small),
        ),
    )
    large_id = upsert_track(
        conn,
        Track(
            external_id="b",
            source="local",
            title="Song",
            artist="Artist",
            file_path=str(large),
        ),
    )
    conn.commit()
    rows = [
        conn.execute("SELECT * FROM tracks WHERE id = ?", (small_id,)).fetchone(),
        conn.execute("SELECT * FROM tracks WHERE id = ?", (large_id,)).fetchone(),
    ]

    result = _dedupe_by_song(rows, limit=25)

    assert result[0]["id"] == large_id


def test_dedupe_by_song_leaves_distinct_songs_untouched():
    conn = connect(":memory:")
    init_db(conn)
    a = upsert_track(
        conn, Track(external_id="a", source="local", title="Song A", artist="Artist")
    )
    b = upsert_track(
        conn, Track(external_id="b", source="local", title="Song B", artist="Artist")
    )
    conn.commit()
    rows = [
        conn.execute("SELECT * FROM tracks WHERE id = ?", (a,)).fetchone(),
        conn.execute("SELECT * FROM tracks WHERE id = ?", (b,)).fetchone(),
    ]

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
    anchor = upsert_track(
        conn, Track(external_id="a", source="local", title="Song", artist="Artist")
    )
    duplicate_copy = upsert_track(
        conn, Track(external_id="b", source="local", title="song", artist="artist")
    )
    other_song = upsert_track(
        conn, Track(external_id="c", source="local", title="Different", artist="Artist")
    )
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


def test_suggest_for_playlist_finds_a_nearby_track_and_excludes_playlist_members():
    conn = connect(":memory:")
    init_db(conn)
    member_a = upsert_track(
        conn,
        Track(
            external_id="a",
            source="local",
            title="A",
            artist="X",
            tempo=120,
            energy=0.5,
        ),
    )
    member_b = upsert_track(
        conn,
        Track(
            external_id="b",
            source="local",
            title="B",
            artist="X",
            tempo=120,
            energy=0.5,
        ),
    )
    near = upsert_track(
        conn,
        Track(
            external_id="near",
            source="local",
            title="Near",
            artist="Y",
            tempo=120,
            energy=0.5,
        ),
    )
    far = upsert_track(
        conn,
        Track(
            external_id="far",
            source="local",
            title="Far",
            artist="Z",
            tempo=120,
            energy=0.5,
        ),
    )
    conn.commit()

    near_vec = [0.0] * EMBEDDING_DIM
    near_vec[0] = 1.0
    far_vec = [0.0] * EMBEDDING_DIM
    far_vec[-1] = 1.0
    for tid in (member_a, member_b, near):
        upsert_embedding(conn, tid, list(near_vec))
    upsert_embedding(conn, far, list(far_vec))
    conn.commit()

    playlist_id = create_playlist(conn, "Mix", [member_a, member_b])
    conn.commit()

    results = suggest_for_playlist(conn, playlist_id)

    ids = [r["id"] for r in results]
    assert member_a not in ids
    assert member_b not in ids
    assert near in ids
    assert ids.index(near) < (ids.index(far) if far in ids else len(ids))


def test_suggest_for_playlist_excludes_a_duplicate_copy_of_a_member_song():
    conn = connect(":memory:")
    init_db(conn)
    member = upsert_track(
        conn, Track(external_id="a", source="local", title="Song", artist="Artist")
    )
    duplicate_copy = upsert_track(
        conn, Track(external_id="b", source="local", title="song", artist="artist")
    )
    other = upsert_track(
        conn, Track(external_id="c", source="local", title="Other", artist="Y")
    )
    conn.commit()

    vec = [0.0] * EMBEDDING_DIM
    vec[0] = 1.0
    for tid in (member, duplicate_copy, other):
        upsert_embedding(conn, tid, list(vec))
    conn.commit()

    playlist_id = create_playlist(conn, "Mix", [member])
    conn.commit()

    results = suggest_for_playlist(conn, playlist_id)

    ids = {r["id"] for r in results}
    assert duplicate_copy not in ids
    assert other in ids


def test_suggest_for_playlist_returns_empty_for_an_empty_playlist():
    conn = connect(":memory:")
    init_db(conn)
    playlist_id = create_playlist(conn, "Empty", [])
    conn.commit()

    assert suggest_for_playlist(conn, playlist_id) == []


def test_album_tracks_orders_by_leading_track_number_in_filename():
    conn = connect(":memory:")
    init_db(conn)
    anchor = upsert_track(
        conn,
        Track(
            external_id="a",
            source="local",
            title="Second",
            artist="Band",
            album="LP",
            file_path="Z:\\music\\Band\\LP\\02 Second.mp3",
        ),
    )
    upsert_track(
        conn,
        Track(
            external_id="b",
            source="local",
            title="First",
            artist="Band",
            album="LP",
            file_path="Z:\\music\\Band\\LP\\01 First.mp3",
        ),
    )
    upsert_track(
        conn,
        Track(
            external_id="c",
            source="local",
            title="Third",
            artist="Band",
            album="LP",
            file_path="Z:\\music\\Band\\LP\\03 Third.mp3",
        ),
    )
    conn.commit()

    results = album_tracks(conn, anchor)

    assert [r["title"] for r in results] == ["First", "Second", "Third"]


def test_album_tracks_orders_multi_disc_albums_by_disc_then_track():
    conn = connect(":memory:")
    init_db(conn)
    # Per-disc folders restart the filename numbering, so only the stored numbers can order
    # these - by filename alone the two discs would interleave (1, 1, 2, 2).
    positions = [("D2T2", 2, 2), ("D1T1", 1, 1), ("D2T1", 2, 1), ("D1T2", 1, 2)]
    ids = [
        upsert_track(
            conn,
            Track(
                external_id=title,
                source="plex",
                title=title,
                artist="Band",
                album="Box Set",
                file_path=f"Box Set/cd {disc}/0{track} {title}.mp3",
                disc_number=disc,
                track_number=track,
            ),
        )
        for title, disc, track in positions
    ]
    conn.commit()

    results = album_tracks(conn, ids[0])

    assert [r["title"] for r in results] == ["D1T1", "D1T2", "D2T1", "D2T2"]


def test_album_tracks_uses_stored_track_number_over_filename():
    conn = connect(":memory:")
    init_db(conn)
    anchor = upsert_track(
        conn,
        Track(
            external_id="a",
            source="plex",
            title="Opener",
            artist="Band",
            album="LP",
            file_path="Band - Opener.mp3",
            track_number=1,
        ),
    )
    upsert_track(
        conn,
        Track(
            external_id="b",
            source="local",
            title="Closer",
            artist="Band",
            album="LP",
            file_path="02 Closer.mp3",
        ),
    )
    conn.commit()

    results = album_tracks(conn, anchor)

    assert [r["title"] for r in results] == ["Opener", "Closer"]


def test_album_tracks_ignores_tracks_from_other_albums():
    conn = connect(":memory:")
    init_db(conn)
    anchor = upsert_track(
        conn,
        Track(external_id="a", source="local", title="A", artist="Band", album="LP"),
    )
    upsert_track(
        conn,
        Track(
            external_id="b", source="local", title="B", artist="Band", album="Other LP"
        ),
    )
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
    anchor = upsert_track(
        conn,
        Track(
            external_id="a",
            source="local",
            title="Song",
            artist="Band",
            album="LP",
            file_path=str(mp3_path),
        ),
    )
    flac_id = upsert_track(
        conn,
        Track(
            external_id="b",
            source="local",
            title="Song",
            artist="Band",
            album="LP",
            file_path=str(flac_path),
        ),
    )
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
    anchor = upsert_track(
        conn,
        Track(
            external_id="a", source="local", title="Sexy Boy [Radio Edit]", artist="Air"
        ),
    )
    upsert_track(
        conn,
        Track(
            external_id="b",
            source="local",
            title="Sexy Boy [Cassius Radio Mix]",
            artist="Air",
        ),
    )
    upsert_track(
        conn,
        Track(external_id="c", source="local", title="Playground Love", artist="Air"),
    )
    unrelated_artist = upsert_track(
        conn,
        Track(
            external_id="d",
            source="local",
            title="Sexy Boy [Remix]",
            artist="Someone Else",
        ),
    )
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
    anchor = upsert_track(
        conn,
        Track(
            external_id="a",
            source="local",
            title="Song [Radio Edit]",
            artist="Artist",
            file_path=str(mp3_path),
        ),
    )
    flac_id = upsert_track(
        conn,
        Track(
            external_id="b",
            source="local",
            title="Song [Radio Edit]",
            artist="Artist",
            file_path=str(flac_path),
        ),
    )
    conn.commit()

    results = versions(conn, anchor)

    assert len(results) == 1
    assert results[0]["id"] == flac_id


def test_discography_groups_albums_with_track_counts_and_years():
    conn = connect(":memory:")
    init_db(conn)
    upsert_track(
        conn,
        Track(
            external_id="a",
            source="local",
            title="A1",
            artist="Band",
            album="Debut",
            year=2000,
        ),
    )
    upsert_track(
        conn,
        Track(
            external_id="b",
            source="local",
            title="A2",
            artist="Band",
            album="Debut",
            year=2000,
        ),
    )
    upsert_track(
        conn,
        Track(
            external_id="c",
            source="local",
            title="B1",
            artist="Band",
            album="Sophomore",
            year=2003,
        ),
    )
    upsert_track(
        conn,
        Track(
            external_id="d",
            source="local",
            title="X1",
            artist="Other Band",
            album="Unrelated",
            year=2010,
        ),
    )
    conn.commit()

    albums = discography(conn, "Band")

    assert albums == [
        {"album": "Debut", "year": 2000, "track_count": 2},
        {"album": "Sophomore", "year": 2003, "track_count": 1},
    ]


def test_discography_dedupes_song_copies_within_an_album():
    conn = connect(":memory:")
    init_db(conn)
    upsert_track(
        conn,
        Track(
            external_id="a",
            source="local",
            title="Song",
            artist="Band",
            album="LP",
            year=1999,
        ),
    )
    upsert_track(
        conn,
        Track(
            external_id="b",
            source="local",
            title="song",
            artist="band",
            album="lp",
            year=1999,
        ),
    )
    conn.commit()

    albums = discography(conn, "Band")

    assert albums == [{"album": "LP", "year": 1999, "track_count": 1}]


def test_search_does_not_drop_a_filtered_match_that_ranks_poorly_on_leftover_semantic_text(
    monkeypatch,
):
    # Reproduces the real bug: "1990s house classics" -> filter handles genre+year, leaving a
    # weak leftover semantic_query ("classic") that the real match doesn't embed close to. A
    # bounded top-N vector scan intersected with the filter can miss it entirely; search()
    # should do a full ranked scan instead whenever a hard filter is active.
    conn = connect(":memory:")
    init_db(conn)

    match_id = upsert_track(
        conn,
        Track(
            external_id="match",
            source="local",
            title="Deep Cut",
            artist="A",
            genre="House",
        ),
    )
    conn.commit()

    query_vec = [0.0] * EMBEDDING_DIM
    query_vec[0] = 1.0
    far_vec = [0.0] * EMBEDDING_DIM
    far_vec[-1] = 1.0
    upsert_embedding(conn, match_id, far_vec)

    # Noise tracks that don't match the filter but rank closer to the query than the real
    # match - more than old_limit (1 * VECTOR_CANDIDATE_MULTIPLIER) would have allowed through.
    for i in range(10):
        noise_id = upsert_track(
            conn,
            Track(
                external_id=f"noise{i}",
                source="local",
                title=f"Noise {i}",
                artist="B",
                genre="Pop",
            ),
        )
        upsert_embedding(conn, noise_id, list(query_vec))
    conn.commit()

    monkeypatch.setattr("backend.search.engine.is_complex_query", lambda q: True)
    monkeypatch.setattr(
        "backend.search.engine.parse_query",
        lambda q: {"genre_include": ["House"], "semantic_query": "classic"},
    )
    monkeypatch.setattr("backend.search.engine.embed_text", lambda q: query_vec)

    results = search(conn, "1990s house classics", limit=1)

    assert match_id in [r["id"] for r in results]


def test_search_by_tag_genre_is_an_exact_filter_not_a_fuzzy_match(monkeypatch):
    conn = connect(":memory:")
    init_db(conn)

    ska_id = upsert_track(
        conn,
        Track(external_id="ska", source="local", title="A", artist="X", genre="Ska"),
    )
    rock_id = upsert_track(
        conn,
        Track(external_id="rock", source="local", title="B", artist="Y", genre="Rock"),
    )
    conn.commit()

    vec = [0.0] * EMBEDDING_DIM
    vec[0] = 1.0
    upsert_embedding(conn, ska_id, list(vec))
    upsert_embedding(conn, rock_id, list(vec))
    conn.commit()

    monkeypatch.setattr("backend.search.engine.embed_text", lambda q: vec)

    results = search_by_tag(conn, "genre", "Ska")

    ids = [r["id"] for r in results]
    assert ids == [ska_id]


def test_search_by_tag_freeform_tag_matches_exact_token(monkeypatch):
    conn = connect(":memory:")
    init_db(conn)

    track_id = upsert_track(
        conn, Track(external_id="a", source="local", title="A", artist="X")
    )
    conn.commit()
    set_tags_list(conn, track_id, ["road trip", "live"])
    conn.commit()

    vec = [0.0] * EMBEDDING_DIM
    vec[0] = 1.0
    upsert_embedding(conn, track_id, list(vec))
    conn.commit()

    monkeypatch.setattr("backend.search.engine.embed_text", lambda q: vec)

    assert [r["id"] for r in search_by_tag(conn, "tag", "road trip")] == [track_id]
    assert search_by_tag(conn, "tag", "road") == []


def test_numeric_distance_is_neutral_when_either_value_unknown():
    assert _numeric_distance(None, 120, norm=40) == 0.5
    assert _numeric_distance(120, None, norm=40) == 0.5


def test_numeric_distance_scales_with_norm_and_clamps_at_one():
    assert _numeric_distance(100, 100, norm=40) == 0.0
    assert _numeric_distance(100, 120, norm=40) == 0.5
    assert _numeric_distance(100, 500, norm=40) == 1.0


def test_key_distance_exact_match_vs_mismatch_vs_unknown():
    assert _key_distance("E", "E") == 0.0
    assert _key_distance("E", "A") == 1.0
    assert _key_distance(None, "A") == 0.5


def test_similar_prefers_same_era_track_over_equally_text_similar_different_era_track():
    # Two candidates with identical text-embedding distance to the anchor; only year differs.
    # The same-era one should rank first once the numeric year factor is applied.
    conn = connect(":memory:")
    init_db(conn)
    anchor = upsert_track(
        conn,
        Track(external_id="a", source="local", title="Anchor", artist="X", year=1986),
    )
    same_era = upsert_track(
        conn,
        Track(external_id="b", source="local", title="Same Era", artist="Y", year=1988),
    )
    far_era = upsert_track(
        conn,
        Track(external_id="c", source="local", title="Far Era", artist="Z", year=2015),
    )
    conn.commit()

    shared_vec = [0.0] * EMBEDDING_DIM
    shared_vec[0] = 1.0
    for tid in (anchor, same_era, far_era):
        upsert_embedding(conn, tid, shared_vec)
    conn.commit()

    results = similar(conn, anchor, limit=10)

    ids = [r["id"] for r in results]
    assert ids.index(same_era) < ids.index(far_era)
