from backend.info import _extract_year


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
