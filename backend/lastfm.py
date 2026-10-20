"""Thin wrapper around Last.fm's artist.getSimilar - real "people who listened to X also
listened to Y" co-occurrence data from actual listeners, used by backend/recommend.py to ground
discovery suggestions in something other than an LLM's own associations. Stdlib HTTP client
(urllib) rather than adding a new dependency for one simple unauthenticated-beyond-an-api-key GET.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from backend.config import settings

log = logging.getLogger(__name__)

API_URL = "https://ws.audioscrobbler.com/2.0/"


def similar_artists(artist: str, limit: int = 10) -> list[dict]:
    """Returns [{"name": str, "match": float 0-1}, ...], best match first. Empty list - logged,
    never raised - on any failure (no API key, network error, unknown artist, bad response):
    this is enrichment for a periodic background job, not something that should ever break a
    reindex."""
    if not settings.lastfm_api_key:
        return []

    params = urllib.parse.urlencode(
        {
            "method": "artist.getsimilar",
            "artist": artist,
            "api_key": settings.lastfm_api_key,
            "format": "json",
            "limit": limit,
        }
    )
    try:
        with urllib.request.urlopen(f"{API_URL}?{params}", timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring: never raised
        log.warning("Last.fm similar-artists lookup failed for %r: %s", artist, exc)
        return []

    raw = data.get("similarartists", {}).get("artist", [])
    if not isinstance(raw, list):
        return []
    results = []
    for a in raw:
        name = a.get("name")
        if not name:
            continue
        try:
            match = float(a.get("match", 0))
        except (TypeError, ValueError):
            match = 0.0
        results.append({"name": name, "match": match})
    return results
