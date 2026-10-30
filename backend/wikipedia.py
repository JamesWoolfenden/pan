"""The opening summary of a specific Wikipedia article, via Wikipedia's REST API - used to give
the "Tell me more" briefing (backend/info.py) real source text about an album rather than
leaving it to Claude's memory alone. Only ever fetches an article MusicBrainz has linked to that
exact album (backend/musicbrainz.py's album_links), never a search. Stdlib HTTP client (urllib),
same as backend/lastfm.py.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from urllib.parse import urlsplit

from backend.musicbrainz import USER_AGENT

log = logging.getLogger(__name__)


def article_summary(article_url: str) -> str | None:
    """Plain-text lead summary of the article at a https://<lang>.wikipedia.org/wiki/<Title>
    URL. None (logged, never raised) for any other kind of URL or on any failure - the
    briefing just goes ahead without it."""
    parts = urlsplit(article_url)
    if not parts.netloc.endswith(".wikipedia.org") or not parts.path.startswith(
        "/wiki/"
    ):
        return None
    title = parts.path[len("/wiki/") :]
    req = urllib.request.Request(
        f"https://{parts.netloc}/api/rest_v1/page/summary/{title}",
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring: never raised
        log.warning("Wikipedia summary lookup failed for %s: %s", article_url, exc)
        return None
    return (data.get("extract") or "").strip() or None
