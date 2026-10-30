from backend import wikipedia


def test_article_summary_reads_the_rest_summary_of_that_exact_article(mocker):
    resp = mocker.MagicMock()
    resp.__enter__.return_value.read.return_value = b'{"extract": " An album. "}'
    urlopen = mocker.patch.object(
        wikipedia.urllib.request, "urlopen", return_value=resp
    )

    summary = wikipedia.article_summary(
        "https://en.wikipedia.org/wiki/Dept._of_Disappearance"
    )

    assert summary == "An album."
    assert (
        urlopen.call_args.args[0].full_url
        == "https://en.wikipedia.org/api/rest_v1/page/summary/Dept._of_Disappearance"
    )


def test_article_summary_ignores_anything_but_a_wikipedia_article(mocker):
    urlopen = mocker.patch.object(wikipedia.urllib.request, "urlopen")

    assert wikipedia.article_summary("https://www.bbc.co.uk/music/reviews/c6fd") is None
    assert wikipedia.article_summary("https://en.wikipedia.org/w/index.php") is None
    urlopen.assert_not_called()


def test_article_summary_is_none_when_the_lookup_fails(mocker):
    mocker.patch.object(
        wikipedia.urllib.request, "urlopen", side_effect=OSError("network down")
    )

    assert wikipedia.article_summary("https://en.wikipedia.org/wiki/X") is None
