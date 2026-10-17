from backend.db import Track, connect, init_db, upsert_track
from scripts.split_compilation_titles import _split_title, migrate


def test_split_title_handles_numbered_prefix():
    assert _split_title("002 - The Rolling Stones - Satisfaction") == (
        "The Rolling Stones",
        "Satisfaction",
    )


def test_split_title_handles_plain_artist_dash_title():
    assert _split_title("The Beatles - Eleanor Rigby") == (
        "The Beatles",
        "Eleanor Rigby",
    )


def test_split_title_does_not_split_on_bare_hyphen_inside_a_name():
    assert _split_title("363 - T-Rex - Bang A Gong Get It On") == (
        "T-Rex",
        "Bang A Gong Get It On",
    )


def test_split_title_strips_a_stray_leading_number_left_in_the_title():
    assert _split_title("320 - Derek and the Dominos - 10 - Bell Bottom Blues") == (
        "Derek and the Dominos",
        "Bell Bottom Blues",
    )


def test_split_title_returns_none_when_already_a_plain_title():
    assert _split_title("Black Magic Woman") is None


def test_migrate_fixes_mislabeled_tracks_in_the_given_album():
    conn = connect(":memory:")
    init_db(conn)
    rolling_stones = upsert_track(
        conn,
        Track(
            external_id="a",
            source="local",
            artist="Santana",
            album="Comp",
            title="002 - The Rolling Stones - Satisfaction",
        ),
    )
    actual_santana = upsert_track(
        conn,
        Track(
            external_id="b",
            source="local",
            artist="Santana",
            album="Comp",
            title="Black Magic Woman",
        ),
    )
    other_album = upsert_track(
        conn,
        Track(
            external_id="c",
            source="local",
            artist="Someone",
            album="Different Album",
            title="Not Touched",
        ),
    )
    conn.commit()

    counts = migrate(conn, "Comp")
    conn.commit()

    fixed = conn.execute(
        "SELECT * FROM tracks WHERE id = ?", (rolling_stones,)
    ).fetchone()
    assert fixed["artist"] == "The Rolling Stones"
    assert fixed["title"] == "Satisfaction"

    untouched = conn.execute(
        "SELECT * FROM tracks WHERE id = ?", (actual_santana,)
    ).fetchone()
    assert untouched["artist"] == "Santana"
    assert untouched["title"] == "Black Magic Woman"

    other = conn.execute("SELECT * FROM tracks WHERE id = ?", (other_album,)).fetchone()
    assert other["artist"] == "Someone"
    assert other["title"] == "Not Touched"

    assert counts == {"fixed": 1, "no_separator_skipped": 1}
