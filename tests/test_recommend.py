import json

from backend import recommend
from backend.db import Track, connect, init_db, upsert_track
from backend.recommend import (
    _album_key,
    _already_owned,
    _salvage_objects,
    find_gap_fill,
)


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


def test_salvage_objects_keeps_complete_entries_of_a_truncated_array():
    # The shape of a real discovery response cut off at max_tokens.
    text = (
        "[\n"
        '  {"artist": "Mary Wells", "reasoning": "Motown, {braces} and \\"quotes\\"."},\n'
        '  {"artist": "Jr. Walker & The All Stars", "reasoning": "Sax-driven."},\n'
        '  {"artist": "The Melodians", "sources_note": "'
    )

    assert [i["artist"] for i in _salvage_objects(text)] == [
        "Mary Wells",
        "Jr. Walker & The All Stars",
    ]


def test_salvage_objects_returns_nothing_without_an_array():
    assert _salvage_objects("Sorry, I can't help with that.") == []


def _mb(title, year, group_id=None):
    return {
        "id": group_id,
        "title": title,
        "year": year,
        "url": f"https://musicbrainz.org/release-group/{group_id}"
        if group_id
        else None,
    }


MUSICBRAINZ_ALBUMS = {
    "Jason Lytle": [
        _mb("Yours Truly, the Commuter", 2009),
        _mb("Dept. of Disappearance", 2012, "dept"),
    ],
    "Grandaddy": [
        _mb("Under the Western Freeway", 1997),
        _mb("The Sophtware Slump", 2000),
        _mb("Last Place", 2017),
    ],
}
WIKIPEDIA = "https://en.wikipedia.org/wiki/Dept._of_Disappearance"


def _gap_fill_library():
    conn = connect(":memory:")
    init_db(conn)
    _album(conn, "Grandaddy", "The Sophtware Slump [+Digital Booklet]")
    _album(conn, "Grandaddy", "Under the Western Freeway")
    _album(conn, "Grandaddy", "Last Place")
    _album(conn, "Jason Lytle", "Yours Truly, the Commuter")
    conn.commit()
    return conn


def _mock_gap_fill(mocker, suggestions, musicbrainz_albums=MUSICBRAINZ_ALBUMS):
    mocker.patch.object(recommend, "_client")
    mocker.patch.object(recommend, "_ask", return_value=suggestions)
    mocker.patch.object(
        recommend.musicbrainz,
        "artist_albums",
        side_effect=lambda artist: (
            None if musicbrainz_albums is None else musicbrainz_albums.get(artist, [])
        ),
    )
    mocker.patch.object(
        recommend.musicbrainz,
        "album_links",
        side_effect=lambda group_id: {"wikipedia": WIKIPEDIA} if group_id else {},
    )


def test_find_gap_fill_drops_owned_and_invented_albums(mocker):
    conn = _gap_fill_library()
    _mock_gap_fill(
        mocker,
        [
            {
                "artist": "Jason Lytle",
                "missing_albums": [
                    "Grandaddy - The Sophtware Slump",  # owned, under the band's name
                    "Joy Square",  # not a real album
                    "Dept of Disappearance",  # real, loosely spelled
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

    # An artist left with nothing real and unowned is dropped; what's kept is MusicBrainz's
    # own record - title, year, its page there, and the write-up it links that album to.
    assert [i["artist"] for i in items] == ["Jason Lytle"]
    assert json.loads(items[0]["detail"]) == {
        "missing_albums": [
            {
                "title": "Dept. of Disappearance",
                "year": 2012,
                "url": "https://musicbrainz.org/release-group/dept",
                "wikipedia": WIKIPEDIA,
            }
        ]
    }


def test_find_gap_fill_shows_nothing_unverified_when_musicbrainz_is_down(mocker):
    conn = _gap_fill_library()
    _mock_gap_fill(
        mocker,
        [
            {
                "artist": "Jason Lytle",
                "missing_albums": ["Dept. of Disappearance"],
                "reasoning": "x",
            }
        ],
        musicbrainz_albums=None,
    )

    assert find_gap_fill(conn, [{"artist": "Jason Lytle"}]) == []
