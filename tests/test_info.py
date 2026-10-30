from backend import info
from backend.info import _extract_year, get_track_background


def test_extract_year_parses_trailing_year_line():
    text, year = _extract_year("Some background about the track.\n\nYEAR: 1998")
    assert text == "Some background about the track."
    assert year == 1998


def test_extract_year_returns_none_when_no_year_line():
    text, year = _extract_year("Just a plain writeup with no trailing metadata.")
    assert text == "Just a plain writeup with no trailing metadata."
    assert year is None


def test_extract_year_ignores_years_mentioned_mid_text():
    text, year = _extract_year("Released in 1998, it became a classic.")
    assert text == "Released in 1998, it became a classic."
    assert year is None


def _row(year=None):
    return {
        "title": "Get Up and Go",
        "artist": "Jason Lytle",
        "album": "Dept. of Disappearance",
        "year": year,
    }


def _mock_claude(mocker, reply):
    mocker.patch.object(info.settings, "anthropic_api_key", "test-key")
    client = mocker.patch.object(info.anthropic, "Anthropic").return_value
    block = mocker.MagicMock(type="text", text=reply)
    client.messages.create.return_value.content = [block]
    return client.messages.create


def test_background_is_grounded_in_the_albums_wikipedia_article(mocker):
    create = _mock_claude(mocker, "A briefing.\nYEAR: 1999")
    mocker.patch.object(
        info.musicbrainz,
        "find_album",
        return_value={
            "title": "Dept. of Disappearance",
            "year": 2012,
            "url": "https://musicbrainz.org/release-group/dept",
            "wikipedia": "https://en.wikipedia.org/wiki/Dept._of_Disappearance",
            "review": "https://www.bbc.co.uk/music/reviews/c6fd",
        },
    )
    mocker.patch.object(
        info.wikipedia,
        "article_summary",
        return_value="Dept. of Disappearance is the second solo album by Jason Lytle.",
    )

    background = get_track_background(_row())

    prompt = create.call_args.kwargs["messages"][0]["content"]
    assert "second solo album by Jason Lytle" in prompt
    assert "Year: 2012" in prompt
    assert background.text == "A briefing."
    # MusicBrainz's release date wins over a year Claude states.
    assert background.inferred_year == 2012
    assert background.links == {
        "wikipedia": "https://en.wikipedia.org/wiki/Dept._of_Disappearance",
        "review": "https://www.bbc.co.uk/music/reviews/c6fd",
        "musicbrainz": "https://musicbrainz.org/release-group/dept",
    }


def test_background_without_a_musicbrainz_match_works_as_before(mocker):
    create = _mock_claude(mocker, "A briefing.\nYEAR: 2012")
    mocker.patch.object(info.musicbrainz, "find_album", return_value=None)
    summary = mocker.patch.object(info.wikipedia, "article_summary")

    background = get_track_background(_row())

    assert "Reference" not in create.call_args.kwargs["messages"][0]["content"]
    summary.assert_not_called()
    assert background.inferred_year == 2012
    assert background.links == {}


def test_background_never_infers_a_year_the_catalog_already_has(mocker):
    _mock_claude(mocker, "A briefing.")
    mocker.patch.object(
        info.musicbrainz,
        "find_album",
        return_value={"title": "x", "year": 2012, "url": None},
    )

    assert get_track_background(_row(year=2011)).inferred_year is None
