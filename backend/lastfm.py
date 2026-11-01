"""Thin wrapper around Last.fm's artist.getSimilar - real "people who listened to X also
listened to Y" co-occurrence data from actual listeners, used by backend/recommend.py to ground
discovery suggestions in something other than an LLM's own associations. Stdlib HTTP client
(urllib) rather than adding a new dependency for one simple unauthenticated-beyond-an-api-key GET.
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
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


# --- Scrobbling (authenticated writes) ---------------------------------------------------------

# Last.fm error codes worth retrying later rather than discarding the plays: 9 invalid session
# (fixable by re-running scripts/lastfm_auth.py), 11/16 service temporarily unavailable, 29 rate
# limit. Anything else (e.g. 6 invalid parameters) won't get better by resending.
RETRYABLE_ERRORS = {9, 11, 16, 29}


def scrobbling_enabled() -> bool:
    return bool(
        settings.lastfm_api_key
        and settings.lastfm_api_secret
        and settings.lastfm_session_key
    )


def _sign(params: dict) -> str:
    """Last.fm's api_sig: md5 of every param (except format/callback) as name+value, sorted by
    name, followed by the shared secret."""
    raw = "".join(
        f"{k}{params[k]}" for k in sorted(params) if k not in ("format", "callback")
    )
    return hashlib.md5((raw + settings.lastfm_api_secret).encode("utf-8")).hexdigest()


def _post_signed(params: dict) -> dict:
    """POSTs a signed call; returns the decoded JSON. Raises on network failure - callers decide
    what that means (scrobble queue: keep and retry)."""
    params = {**params, "api_key": settings.lastfm_api_key}
    params["api_sig"] = _sign(params)
    params["format"] = "json"
    body = urllib.parse.urlencode(params).encode("utf-8")
    try:
        with urllib.request.urlopen(API_URL, data=body, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Last.fm returns API errors as 4xx/5xx with a JSON body; surface that body if present.
        try:
            return json.loads(exc.read())
        except Exception:  # noqa: BLE001
            raise exc


def get_mobile_session(username: str, password: str) -> str:
    """One-time auth (scripts/lastfm_auth.py): exchanges username+password for a permanent
    session key. Raises RuntimeError with Last.fm's message on failure."""
    data = _post_signed(
        {"method": "auth.getMobileSession", "username": username, "password": password}
    )
    session = data.get("session", {}).get("key")
    if not session:
        raise RuntimeError(data.get("message", f"unexpected response: {data}"))
    return session


def scrobble(entries: list[dict]) -> bool:
    """Submits up to 50 {artist, track, album?, duration?, timestamp} plays in one call.
    Returns True when Last.fm took them (or they can never be accepted), False when they should
    be kept and retried. Never raises."""
    if not entries:
        return True
    params: dict = {"method": "track.scrobble", "sk": settings.lastfm_session_key}
    for i, e in enumerate(entries):
        params[f"artist[{i}]"] = e["artist"]
        params[f"track[{i}]"] = e["track"]
        params[f"timestamp[{i}]"] = e["timestamp"]
        if e.get("album"):
            params[f"album[{i}]"] = e["album"]
        if e.get("duration"):
            params[f"duration[{i}]"] = e["duration"]
    try:
        data = _post_signed(params)
    except Exception as exc:  # noqa: BLE001 - network down etc: keep the plays queued
        log.warning("Last.fm scrobble failed, will retry: %s", exc)
        return False
    code = data.get("error")
    if code is None:
        return True
    log.warning("Last.fm scrobble error %s: %s", code, data.get("message"))
    return code not in RETRYABLE_ERRORS
