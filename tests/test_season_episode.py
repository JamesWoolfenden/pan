from pathlib import Path

from backend.ingest.local_source import _parse_season_episode


def test_parse_season_episode_sxxexx_in_filename():
    path = Path("tv/Some Show/Season 01/Some Show S01E03 Title.mkv")
    assert _parse_season_episode(path) == (1, 3)


def test_parse_season_episode_real_example():
    path = Path(
        "tv/I Jack Wright/Season 01/"
        "I Jack Wright 2025 S01E03 A Tougher Love 720p WEB-DL HEVC x265 BONE.mkv"
    )
    assert _parse_season_episode(path) == (1, 3)


def test_parse_season_episode_lowercase_compact_form():
    path = Path("tv/Some Show/Season 01/some.show.s1e3.title.mkv")
    assert _parse_season_episode(path) == (1, 3)


def test_parse_season_episode_nxnn_alt_form():
    path = Path("tv/Some Show/Season 01/Some Show 1x03 Title.mkv")
    assert _parse_season_episode(path) == (1, 3)


def test_parse_season_episode_season_from_folder_episode_from_filename():
    path = Path("tv/Some Show/Season 02/Some Show Episode 05.mkv")
    assert _parse_season_episode(path) == (2, 5)


def test_parse_season_episode_underscore_separated_episode_word():
    # Real example: a scene-release filename separating words with underscores/dots rather
    # than spaces.
    path = Path(
        "tv/The Listeners/Season 01/"
        "The_Listeners_Series_1_-_04._Episode_4_m00255fn_editorial.mp4"
    )
    assert _parse_season_episode(path) == (1, 4)


def test_parse_season_episode_bare_episode_marker_no_season():
    # Real example: a single-season documentary series with no "Season" folder/word anywhere,
    # just a bare "E04" in the filename.
    path = Path("tv/The.1619.Project.2023/The.1619.Project.E04.Capitalism.720p.mkv")
    assert _parse_season_episode(path) == (None, 4)


def test_parse_season_episode_returns_none_for_a_plain_movie():
    path = Path("movies/Dark City (1998) Director's Cut Remastered [dAV1nci].mkv")
    assert _parse_season_episode(path) == (None, None)


def test_parse_season_episode_does_not_mistake_a_year_for_season_episode():
    path = Path("movies/Doctor.Sleep.2019.720p.BluRay.H264.AAC-RARBG.mp4")
    assert _parse_season_episode(path) == (None, None)
