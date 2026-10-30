import urllib.error

from backend import musicbrainz


def _fake_get(responses):
    def get(path, params):
        return responses[path]

    return get


def test_artist_albums_returns_titles_and_years(mocker):
    mocker.patch.object(
        musicbrainz,
        "_get",
        side_effect=_fake_get(
            {
                "artist": {
                    "artists": [{"id": "lytle", "name": "Jason Lytle", "score": 100}]
                },
                "release-group": {
                    "release-groups": [
                        {
                            "id": "abc-123",
                            "title": "Yours Truly, the Commuter",
                            "first-release-date": "2009-05-19",
                        },
                        {"title": "Undated", "first-release-date": ""},
                    ]
                },
            }
        ),
    )

    assert musicbrainz.artist_albums("Jason Lytle") == [
        {
            "id": "abc-123",
            "title": "Yours Truly, the Commuter",
            "year": 2009,
            "url": "https://musicbrainz.org/release-group/abc-123",
        },
        {"id": None, "title": "Undated", "year": None, "url": None},
    ]


def test_album_links_keeps_only_wikipedia_and_review(mocker):
    get = mocker.patch.object(
        musicbrainz,
        "_get",
        return_value={
            "relations": [
                {"type": "discogs", "url": {"resource": "https://discogs.com/m/1"}},
                {"type": "review", "url": {"resource": "https://bbc.co.uk/r/1"}},
                {"type": "review", "url": {"resource": "https://bbc.co.uk/r/2"}},
                {
                    "type": "wikipedia",
                    "url": {"resource": "https://en.wikipedia.org/x"},
                },
            ]
        },
    )

    assert musicbrainz.album_links("dept") == {
        "review": "https://bbc.co.uk/r/1",
        "wikipedia": "https://en.wikipedia.org/x",
    }
    get.assert_called_once_with("release-group/dept", {"inc": "url-rels"})


def test_album_links_falls_back_to_wikidata_and_allmusic(mocker):
    mocker.patch.object(
        musicbrainz,
        "_get",
        return_value={
            "relations": [
                {"type": "allmusic", "url": {"resource": "https://allmusic.com/a/1"}},
                {
                    "type": "wikidata",
                    "url": {"resource": "https://www.wikidata.org/wiki/Q1055992"},
                },
            ]
        },
    )
    resp = mocker.MagicMock()
    # Keyed by a different ID - the entity was merged into Q1 and redirects there.
    resp.__enter__.return_value.read.return_value = (
        b'{"entities": {"Q1": {"sitelinks": {"enwiki": '
        b'{"url": "https://en.wikipedia.org/wiki/Hounds_of_Love"}}}}}'
    )
    urlopen = mocker.patch.object(
        musicbrainz.urllib.request, "urlopen", return_value=resp
    )

    assert musicbrainz.album_links("hounds") == {
        "wikipedia": "https://en.wikipedia.org/wiki/Hounds_of_Love",
        "review": "https://allmusic.com/a/1",
    }
    assert urlopen.call_args.args[0].full_url.endswith(
        "/Special:EntityData/Q1055992.json"
    )


def _discography(mocker, albums):
    mocker.patch.object(musicbrainz, "artist_albums", return_value=albums)
    return mocker.patch.object(
        musicbrainz, "album_links", return_value={"wikipedia": "https://w/x"}
    )


def test_find_album_matches_loosely_and_adds_links(mocker):
    album = {"id": "dept", "title": "Dept. of Disappearance", "year": 2012, "url": "u"}
    links = _discography(mocker, [album])

    assert musicbrainz.find_album("Jason Lytle", "Dept of Disappearance [Deluxe]") == {
        **album,
        "wikipedia": "https://w/x",
    }
    links.assert_called_once_with("dept")


def test_find_album_is_none_for_an_album_not_in_the_discography(mocker):
    links = _discography(
        mocker, [{"id": "a", "title": "Yours Truly", "year": 2009, "url": "u"}]
    )

    assert musicbrainz.find_album("Jason Lytle", "Greatest Hits") is None
    assert musicbrainz.find_album("Jason Lytle", "") is None
    links.assert_not_called()


def test_find_album_is_none_when_musicbrainz_is_down(mocker):
    _discography(mocker, None)

    assert musicbrainz.find_album("Jason Lytle", "Yours Truly") is None


def test_album_links_is_empty_when_the_lookup_fails(mocker):
    mocker.patch.object(musicbrainz, "_get", side_effect=OSError("network down"))

    assert musicbrainz.album_links("dept") == {}


def test_artist_id_prefers_an_exact_name_match_over_rank(mocker):
    mocker.patch.object(
        musicbrainz,
        "_get",
        side_effect=_fake_get(
            {
                "artist": {
                    "artists": [
                        {"id": "tribute", "name": "Jason Lytle Tribute", "score": 100},
                        {"id": "lytle", "name": "Jason Lytle", "score": 98},
                    ]
                }
            }
        ),
    )

    assert musicbrainz._artist_id("jason lytle") == "lytle"


def test_artist_albums_is_empty_for_an_unknown_artist(mocker):
    mocker.patch.object(
        musicbrainz,
        "_get",
        side_effect=_fake_get(
            {"artist": {"artists": [{"id": "x", "name": "Someone", "score": 40}]}}
        ),
    )

    assert musicbrainz.artist_albums("Nobody Real") == []


def test_get_retries_a_503_slow_down(mocker):
    mocker.patch.object(musicbrainz, "MIN_INTERVAL_S", 0)
    mocker.patch.object(musicbrainz.time, "sleep")
    ok = mocker.MagicMock()
    ok.__enter__.return_value.read.return_value = b'{"artists": []}'
    urlopen = mocker.patch.object(
        musicbrainz.urllib.request,
        "urlopen",
        side_effect=[
            urllib.error.HTTPError("url", 503, "Service Unavailable", None, None),
            ok,
        ],
    )

    assert musicbrainz._get("artist", {"query": "x"}) == {"artists": []}
    assert urlopen.call_count == 2


def test_artist_albums_is_none_when_the_lookup_fails(mocker):
    mocker.patch.object(musicbrainz, "_get", side_effect=OSError("network down"))

    assert musicbrainz.artist_albums("Jason Lytle") is None
