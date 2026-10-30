"""Real discography data from MusicBrainz, used by backend/recommend.py to check gap-fill
suggestions before showing them - those come from Claude's own memory of an artist's albums,
which for a less well-known artist can include a plausible-sounding album that doesn't exist.
Free and keyless; stdlib HTTP client (urllib) for the same reason as backend/lastfm.py.

MusicBrainz asks clients to identify themselves with a User-Agent and stay under one request
per second (https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting) - both done here.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

API_URL = "https://musicbrainz.org/ws/2/"
SITE_URL = "https://musicbrainz.org/"
USER_AGENT = "pan/0.1 ( https://github.com/JamesWoolfenden/pan )"
MIN_INTERVAL_S = 1.1
# Search score (0-100) below which the top artist hit is treated as "not this artist" rather
# than risk checking suggestions against some other artist's discography.
MIN_ARTIST_SCORE = 90

_lock = threading.Lock()
_last_request = 0.0


def _throttle() -> None:
    global _last_request
    with _lock:
        wait = _last_request + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def _get(path: str, params: dict, retries: int = 2) -> dict:
    query = urllib.parse.urlencode({**params, "fmt": "json"})
    req = urllib.request.Request(
        f"{API_URL}{path}?{query}", headers={"User-Agent": USER_AGENT}
    )
    for attempt in range(retries + 1):
        _throttle()
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            # 503 is how MusicBrainz says "slow down" (and also briefly overloaded) - back off
            # and try again rather than treat the artist as unverifiable.
            if exc.code != 503 or attempt == retries:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def _artist_id(artist: str) -> str | None:
    data = _get("artist", {"query": f'artist:"{artist}"', "limit": 5})
    hits = data.get("artists") or []
    wanted = artist.strip().casefold()
    # Prefer an exact name match among the confident hits over whatever ranked first.
    confident = [h for h in hits if int(h.get("score", 0)) >= MIN_ARTIST_SCORE]
    for hit in confident:
        if (hit.get("name") or "").strip().casefold() == wanted:
            return hit.get("id")
    return confident[0].get("id") if confident else None


def artist_albums(artist: str) -> list[dict] | None:
    """[{"id": str | None, "title": str, "year": int | None, "url": str | None}, ...] - every
    release group MusicBrainz files as an album for this artist, url being its page on
    musicbrainz.org. None (logged, never raised) when the lookup itself failed, which a caller
    must treat as "couldn't check", not "has no albums"; an empty list means MusicBrainz
    answered but has no albums for them (or doesn't know the artist)."""
    try:
        artist_id = _artist_id(artist)
        if artist_id is None:
            return []
        data = _get(
            "release-group", {"artist": artist_id, "type": "album", "limit": 100}
        )
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring: never raised
        log.warning("MusicBrainz album lookup failed for %r: %s", artist, exc)
        return None

    albums = []
    for group in data.get("release-groups") or []:
        title = group.get("title")
        if not title:
            continue
        date = group.get("first-release-date") or ""
        group_id = group.get("id")
        albums.append(
            {
                "id": group_id,
                "title": title,
                "year": int(date[:4]) if date[:4].isdigit() else None,
                "url": f"{SITE_URL}release-group/{group_id}" if group_id else None,
            }
        )
    return albums


def album_links(group_id: str) -> dict:
    """{"wikipedia": url, "review": url} - whichever of these MusicBrainz's editors have
    attached to this exact album (release group), for something that says what the music is
    actually like; MusicBrainz's own page is a catalog entry. Only links recorded against the
    album itself, never a search. Empty on failure (logged, never raised) - the album is
    already verified by then, so it's still worth showing, just without these extras."""
    try:
        data = _get(f"release-group/{group_id}", {"inc": "url-rels"})
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring: never raised
        log.warning("MusicBrainz link lookup failed for %s: %s", group_id, exc)
        return {}

    links: dict[str, str] = {}
    for relation in data.get("relations") or []:
        kind = relation.get("type")
        url = (relation.get("url") or {}).get("resource")
        if kind in ("wikipedia", "review") and url and kind not in links:
            links[kind] = url
    return links
