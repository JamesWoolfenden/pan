import json

from backend import recommend
from backend.db import Track, connect, init_db, upsert_track
from backend.recommend import _album_key, _already_owned, find_gap_fill


def _album(conn, artist, album):
    upsert_track(
        conn,
        Track(
            external_id=f"{artist}/{album}",
            source="local",
            title=f"{album} track",
            artist=artist,
            album=album,
            file_path=f"Z:\\music\\{artist}\\{album}.mp3",
        ),
    )


def test_album_key_ignores_case_punctuation_and_edition_tags():
    assert _album_key("The Sophtware Slump [+Digital Booklet]") == _album_key(
        "the sophtware slump"
    )
    assert _album_key("Last Place (Deluxe Edition) [Remastered]") == _album_key(
        "Last Place"
    )
    assert _album_key("He's Simple, He's Dumb") == _album_key("Hes Simple Hes Dumb")


def test_album_key_keeps_distinct_albums_distinct():
    assert _album_key("The Sophtware Slump") != _album_key(
        "The Sophtware Slump ..... on a wooden piano"
    )


def test_already_owned_matches_an_artist_prefixed_suggestion():
    owned = {_album_key("The Sophtware Slump [+Digital Booklet]")}
    assert _already_owned("Grandaddy - The Sophtware Slump", owned)
    assert _already_owned("The Sophtware Slump", owned)
    assert not _already_owned("Nine Clouds", owned)


def test_find_gap_fill_drops_albums_owned_under_another_artist(mocker):
    conn = connect(":memory:")
    init_db(conn)
    _album(conn, "Grandaddy", "The Sophtware Slump [+Digital Booklet]")
    _album(conn, "Grandaddy", "Under the Western Freeway")
    _album(conn, "Grandaddy", "Last Place")
    _album(conn, "Jason Lytle", "Yours Truly, the Commuter")
    conn.commit()

    mocker.patch.object(recommend, "_client")
    mocker.patch.object(
        recommend,
        "_ask",
        return_value=[
            {
                "artist": "Jason Lytle",
                "missing_albums": [
                    "Grandaddy - The Sophtware Slump",
                    "Grandaddy - Under the Western Freeway",
                    "Nine Clouds",
                ],
                "reasoning": "x",
            },
            {
                "artist": "Grandaddy",
                "missing_albums": ["Last Place"],
                "reasoning": "y",
            },
        ],
    )

    items = find_gap_fill(conn, [{"artist": "Jason Lytle"}, {"artist": "Grandaddy"}])

    # Owned albums are filtered out, and an artist left with nothing missing is dropped.
    assert [i["artist"] for i in items] == ["Jason Lytle"]
    assert json.loads(items[0]["detail"]) == {"missing_albums": ["Nine Clouds"]}
