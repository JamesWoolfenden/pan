"""Finds a YouTube match for a track, for video-mode playback - picks whichever of the top
text-search results has a duration (fetched separately; search results don't include it)
closest to this track's own known duration, preferring any plausible mix/edit of the right song
over finding nothing at all. Duration is used to rank candidates, not to reject them outright -
the text search for "{artist} {title}" is doing the real work of finding the right song; duration
just picks the best cut of it (radio edit vs. extended mix vs. whatever this library's copy is).

Costs 100 quota units per lookup (a search.list call) against the YouTube Data API's default
10,000/day free quota - looked up lazily per-track, same as lyrics/AI background, never
backfilled in bulk.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import urllib.parse
import urllib.request

from backend.config import settings

log = logging.getLogger(__name__)

API_URL = "https://www.googleapis.com/youtube/v3"
_ISO8601_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")
_OFFICIAL_VIDEO_RE = re.compile(r"official.*video")
# A "- Topic" channel (YouTube's auto-generated, Content-ID-driven upload for a track) is almost
# always a static image, not an actual video - a real official music video is nicer for video
# mode when one's a close enough duration match too. Tried as a flat penalty on Topic channels
# first, but that's the wrong lever: it can let an unverified reupload with a slightly worse
# duration match outrank the official Topic channel just because Topic got docked points, even
# though nothing about that reupload is actually more trustworthy. Reward the specific, reliable
# signal of an official video instead (VEVO, or "official ... video" in the title, loosely
# matched so "Official HD Video"/"Official 4K Video"/"Official Music Video" etc. all count, not
# just an exact "official video" substring) - this only ever helps an identifiably official
# upload win, never helps an anonymous one beat a Topic channel on duration alone.
#
# Large (not a tie-breaking nudge) because the single/official-video edit of a track and a
# library copy pulled from a 12" extended mix or DJ compilation can legitimately differ by
# several minutes - a real official video is still usually the better video-mode pick even then,
# per user preference ("I'd rather have any mix than none" / "compilation be damned").
OFFICIAL_VIDEO_BONUS_SECONDS = 180


def _is_official_video(item: dict) -> bool:
    channel = item.get("snippet", {}).get("channelTitle", "").lower()
    title = item.get("snippet", {}).get("title", "").lower()
    return "vevo" in channel or bool(_OFFICIAL_VIDEO_RE.search(title))


def _parse_duration_seconds(iso_duration: str) -> int | None:
    match = _ISO8601_DURATION_RE.fullmatch(iso_duration)
    if not match:
        return None
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _get(path: str, **params) -> dict:
    params["key"] = settings.youtube_api_key
    url = f"{API_URL}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def rank_matches(row: sqlite3.Row) -> list[str]:
    """Returns candidate video ids ranked best-first, or [] only when there's genuinely nothing
    to go on (no YOUTUBE_API_KEY configured, no artist/title to search with, the text search
    itself returned zero results, or the lookup failed) - logged, never raised, since this is
    enrichment for an optional UI feature, not something that should break playback.

    Returns the full ranking (not just the winner) because the YouTube Data API's "embeddable"
    flag isn't fully reliable - a candidate that looks fine can still fail to actually play once
    embedded. Callers cache the runner-ups (see db.set_youtube_video_id) so a dismiss can try the
    next-best one without another search.list call (quota is scarce: 100/day on the free tier)."""
    if not settings.youtube_api_key:
        return []

    query = f"{row['artist'] or ''} {row['title'] or ''}".strip()
    if not query:
        return []

    try:
        # A larger pool than strictly needed, since the embeddable filter below can eliminate
        # some candidates outright - costs nothing extra (search.list is a flat 100 units
        # regardless of maxResults; videos.list is ~free per id, batched in one call).
        search = _get("search", part="snippet", type="video", maxResults=8, q=query)
        candidate_ids = [item["id"]["videoId"] for item in search.get("items", [])]
        if not candidate_ids:
            return []
        details = _get(
            "videos", part="snippet,contentDetails,status", id=",".join(candidate_ids)
        )
    except Exception as exc:  # noqa: BLE001 - network/API/shape errors are all "this lookup
        # didn't work", and a video match is best-effort enrichment, never worth crashing over.
        log.warning("YouTube lookup failed for track %s: %s", row["id"], exc)
        return []

    items = details.get("items", [])
    if not items:
        return []

    # Many official/label-managed uploads (especially the "- Topic" auto-generated channels)
    # have embedding disabled - they play fine on youtube.com but show "Video unavailable"
    # inside video mode's iframe. Prefer embeddable candidates; only fall back to
    # non-embeddable ones (still useful for the Info panel's "open on YouTube" button, and video
    # mode's dismiss button is the escape hatch if it turns out broken anyway) when none qualify.
    embeddable = [
        item for item in items if item.get("status", {}).get("embeddable", True)
    ]
    pool = embeddable or items

    # Duration ranks candidates when this track's own duration is known - otherwise (rare: a
    # track whose audio features/metadata extraction hasn't run) there's nothing to rank by, so
    # just trust the text search's own ordering (within the embeddable-preferred pool) rather
    # than finding nothing.
    if not row["duration_ms"]:
        return [item["id"] for item in pool]

    target_seconds = row["duration_ms"] / 1000
    scored = []
    for item in pool:
        seconds = _parse_duration_seconds(item["contentDetails"]["duration"])
        if seconds is None:
            continue
        score = abs(seconds - target_seconds)
        if _is_official_video(item):
            score -= OFFICIAL_VIDEO_BONUS_SECONDS
        scored.append((score, item["id"]))
    scored.sort(key=lambda pair: pair[0])
    return [video_id for _, video_id in scored]
