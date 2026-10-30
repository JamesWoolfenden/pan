"""Library-improvement suggestions, derived from actual listening behavior rather than genre
tags alone: play count x completion rate ("love score") finds what's genuinely resonating, not
just what's been sampled a lot (a frequently-skipped artist ranks low despite high play count).

Two kinds of suggestion:
- gap_fill: an artist already loved, but represented here mostly by compilations - which studio
  albums are missing from their actual discography.
- discovery: new artists not in the library at all, adjacent to the confirmed taste profile.

gap_fill leans on Claude's own music knowledge (same pattern as backend/tagging/cloud_tagger.py)
- there's no local data that could answer "what are this artist's real studio albums" - but
checks every suggested album against MusicBrainz (backend/musicbrainz.py) before showing it,
since that memory can invent plausible albums for a less well-known artist. discovery
grounds itself in real listener co-occurrence data from Last.fm when LASTFM_API_KEY is
configured (see backend/lastfm.py) rather than purely an LLM's own associations, falling back to
asking Claude directly otherwise. Cheap either way: a handful of calls per run, not one per track.
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3

import anthropic

from backend import musicbrainz
from backend.config import settings
from backend.db import (
    artist_albums,
    decided_recommendation_artists,
    distinct_album_titles,
    distinct_artists,
    previously_recommended_artists,
    top_genres_moods,
    top_loved_artists,
)
from backend.lastfm import similar_artists
from backend.llm_json import strip_code_fence

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-5-5"

GAP_FILL_PROMPT = """For each artist below, you're given the albums already in a music library
(mostly pulled from "best of"/compilation discs). Using your knowledge of their real
discography, list up to 3 major studio albums that are NOT already represented (by any version
of the title - be lenient about exact naming) and are worth adding for someone who clearly likes
this artist. Only list albums released under that exact artist name - not records by bands or
side projects they were also part of, which are filed under their own name in this library and
may well be owned already. Skip an artist entirely (omit it) if their discography is already well covered, or
if they're primarily a singles/compilation act with no distinct studio albums worth singling out.

Artists:
{artists_json}

Respond with ONLY a JSON array of {{"artist": <string>, "missing_albums": [<string>, ...],
"reasoning": <one sentence>}} objects. Omit artists with nothing worth adding - don't pad the
list to look complete.
"""

DISCOVERY_PROMPT = """A music library's listening data shows this taste profile:

Most-loved artists (ranked by play count x completion rate, i.e. genuinely finished listens,
not just sampled): {loved_artists}

Most common genre tags: {genres}
Most common mood tags: {moods}

Already suggested in a past run - do not repeat any of these: {excluded}

Suggest {limit} artists NOT already well-represented in this library that would fit this taste.
Reserve {stretch} of the {limit} for a deliberate stretch pick: something that doesn't obviously
match the pattern above but is still a plausible fit, so this list doesn't just loop on the same
narrow genre cluster every time - say explicitly in its reasoning why it's a stretch. The rest
should be real discovery genuinely adjacent to the loved-artists list, not generic genre matches.
One sentence of reasoning each, naming which loved artist(s) it connects to.

Respond with ONLY a JSON array of {{"artist": <string>, "reasoning": <one sentence>}} objects.
"""

# Used when Last.fm grounding data is available (see _lastfm_candidate_pool) - Claude's job
# shifts from inventing candidates to curating and explaining real listener co-occurrence data,
# which is an independent signal from an LLM's own associations rather than another flavor of
# the same thing. Falls back to DISCOVERY_PROMPT when no LASTFM_API_KEY is configured, or a run's
# lookups come back empty.
DISCOVERY_GROUNDED_PROMPT = """A music library's listening data shows this taste profile:

Most-loved artists (ranked by play count x completion rate, i.e. genuinely finished listens,
not just sampled): {loved_artists}

Most common genre tags: {genres}
Most common mood tags: {moods}

Below is REAL listener co-occurrence data from Last.fm (not your own guess) - for each loved
artist, other artists their actual listeners also listen to, with a 0-1 match strength and which
loved artist(s) they came up for:

{candidates_json}

Already suggested in a past run - do not repeat any of these: {excluded}

Pick the best {limit} artists from that real data to suggest as new discovery - favor ones tied
to multiple loved artists and higher match scores, but use judgment rather than a strict ranking.
Then add {stretch} more of your own choosing, NOT from the list above, that deliberately stretch
outside what that co-occurrence data surfaced - say explicitly in their reasoning why they're a
stretch. One sentence of reasoning each, naming which loved artist(s) it connects to.

Respond with ONLY a JSON array of {{"artist": <string>, "reasoning": <one sentence>}} objects.
"""


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


# Billed by tokens actually generated, not the cap - a dozen artists with a sentence of reasoning
# each overran the old 1024 and every discovery run came back as unparseable, truncated JSON.
MAX_TOKENS = 16000


def _salvage_objects(text: str) -> list[dict]:
    """The complete {...} entries of a JSON array that was cut off partway through, so a
    truncated response still yields everything before the cut rather than nothing."""
    start = text.find("[")
    if start < 0:
        return []
    decoder = json.JSONDecoder()
    items, pos = [], start + 1
    while True:
        while pos < len(text) and text[pos] in " \t\r\n,":
            pos += 1
        try:
            obj, pos = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            return items
        if isinstance(obj, dict):
            items.append(obj)


def _ask(client: anthropic.Anthropic, prompt: str) -> list[dict]:
    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "between_tools"},
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in message.content if block.type == "text")
    if message.stop_reason == "max_tokens":
        log.warning(
            "Recommendation response hit max_tokens (%d) - truncated", MAX_TOKENS
        )
    try:
        parsed = json.loads(strip_code_fence(text))
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        salvaged = _salvage_objects(text)
        log.warning(
            "Could not parse recommendation response - kept %d complete entries: %s",
            len(salvaged),
            text[:500],
        )
        return salvaged


def _rotate_seed(
    conn: sqlite3.Connection, loved: list[dict], category: str, n: int
) -> list[dict]:
    """Picks n artists from the loved pool, preferring ones never suggested under this category
    before - so a run doesn't just re-surface the same top artists every time. Shuffled within
    the "never suggested" group too, not just taken in rank order, so which of those surfaces
    first also varies run to run. Falls back to the already-suggested group (oldest exclusion
    first doesn't matter here, so just rank order) once the unexplored pool runs out - at that
    point everything loved has been covered at least once, which is fine."""
    already = previously_recommended_artists(conn, category)
    decided = decided_recommendation_artists(conn, category)
    pool = [c for c in loved if c["artist"].strip().lower() not in decided]
    fresh = [c for c in pool if c["artist"].strip().lower() not in already]
    covered = [c for c in pool if c["artist"].strip().lower() in already]
    random.shuffle(fresh)
    return (fresh + covered)[:n]


_album_key = musicbrainz.album_key


def _title_keys(suggested: str) -> set[str]:
    """Claude sometimes writes "Band - Album" (e.g. "Grandaddy - The Sophtware Slump" when
    asked about Jason Lytle), so the part after an "Artist - " prefix is a candidate too."""
    candidates = [suggested]
    if " - " in suggested:
        candidates.append(suggested.split(" - ", 1)[1])
    return {key for c in candidates if (key := _album_key(c))}


def _already_owned(suggested: str, owned_keys: set[str]) -> bool:
    return bool(_title_keys(suggested) & owned_keys)


def _verified_missing(
    artist: str, suggested: list[str], owned_keys: set[str]
) -> list[dict] | None:
    """The suggested albums that are both not already owned and real - matched against the
    artist's MusicBrainz discography, since Claude's own memory of a less well-known artist can
    include albums that don't exist. Returned as MusicBrainz's own record ({"title", "year",
    "url"} - see musicbrainz.artist_albums) plus any "wikipedia"/"review" links it has for the
    album (musicbrainz.album_links). None if MusicBrainz couldn't be reached: unverified
    suggestions are dropped, not shown."""
    known = musicbrainz.artist_albums(artist)
    if known is None:
        return None
    by_key: dict[str, dict] = {}
    for album in known:
        by_key.setdefault(_album_key(album["title"]), album)

    verified: list[dict] = []
    seen_ids: set[str | None] = set()
    for title in suggested:
        if _already_owned(title, owned_keys):
            continue
        match = next((by_key[k] for k in _title_keys(title) if k in by_key), None)
        if match is None:
            log.info(
                "Dropping gap-fill album not on MusicBrainz: %s - %r", artist, title
            )
            continue
        if match["id"] in seen_ids:
            continue
        seen_ids.add(match["id"])
        links = musicbrainz.album_links(match["id"]) if match["id"] else {}
        verified.append(
            {
                "title": match["title"],
                "year": match["year"],
                "url": match["url"],
                **links,
            }
        )
    return verified


def find_gap_fill(
    conn: sqlite3.Connection, loved: list[dict], max_artists: int = 5
) -> list[dict]:
    candidates = _rotate_seed(conn, loved, "gap_fill", max_artists)
    if not candidates:
        return []
    payload = [
        {"artist": c["artist"], "albums_owned": artist_albums(conn, c["artist"])}
        for c in candidates
    ]
    client = _client()
    results = _ask(client, GAP_FILL_PROMPT.format(artists_json=json.dumps(payload)))

    # The prompt only shows each artist's own albums, so a "missing" album can still be in the
    # library under a different artist name (a band vs. its frontman's solo name) - check the
    # whole library rather than trust it.
    owned_keys = {_album_key(a) for a in distinct_album_titles(conn)} - {""}
    items = []
    for r in results:
        if not r.get("artist"):
            continue
        missing = _verified_missing(
            r["artist"], r.get("missing_albums") or [], owned_keys
        )
        if missing is None:
            log.warning(
                "Skipping gap-fill for %s - MusicBrainz unavailable to verify it",
                r["artist"],
            )
            continue
        if missing:
            items.append(
                {
                    "category": "gap_fill",
                    "artist": r["artist"],
                    "reasoning": r.get("reasoning"),
                    "detail": json.dumps({"missing_albums": missing}),
                }
            )
    return items


def _lastfm_candidate_pool(
    loved: list[dict], existing: set[str], excluded: set[str], seed_size: int = 8
) -> list[dict]:
    """Aggregates real Last.fm similar-artist data across a random sample of the loved pool
    (random, not just the top N, so which loved artists get queried also varies run to run) -
    already filtered against what's owned and what's been suggested before. An artist turning up
    as similar to several loved artists ranks above one tied to just one. Empty if no
    LASTFM_API_KEY is configured, or every lookup happens to come back empty."""
    seed = random.sample(loved, min(seed_size, len(loved)))
    pool: dict[str, dict] = {}
    for c in seed:
        for similar in similar_artists(c["artist"]):
            key = similar["name"].strip().lower()
            if key in existing or key in excluded:
                continue
            entry = pool.setdefault(
                key, {"artist": similar["name"], "sources": [], "best_match": 0.0}
            )
            entry["sources"].append(c["artist"])
            entry["best_match"] = max(entry["best_match"], similar["match"])
    return sorted(
        pool.values(), key=lambda e: (len(e["sources"]), e["best_match"]), reverse=True
    )[:25]


def find_discovery(
    conn: sqlite3.Connection, loved: list[dict], limit: int = 10
) -> list[dict]:
    if not loved:
        return []
    already_suggested = previously_recommended_artists(conn, "discovery")
    existing = distinct_artists(conn)
    genre_mood = top_genres_moods(conn)
    client = _client()
    stretch = max(1, limit // 5)
    common_args = {
        "loved_artists": ", ".join(c["artist"] for c in loved),
        "genres": ", ".join(genre_mood["genres"]),
        "moods": ", ".join(genre_mood["moods"]),
        "excluded": ", ".join(sorted(already_suggested)) or "(none yet)",
        "limit": limit,
        "stretch": stretch,
    }

    pool = _lastfm_candidate_pool(loved, existing, already_suggested)
    if pool:
        prompt = DISCOVERY_GROUNDED_PROMPT.format(
            candidates_json=json.dumps(pool), **common_args
        )
    else:
        prompt = DISCOVERY_PROMPT.format(**common_args)
    results = _ask(client, prompt)

    # Claude's knowledge of exactly what's already in the library/already suggested is only as
    # good as what fit in the prompt - cross-check against the real, full sets rather than trust
    # it to have actually followed those instructions.
    return [
        {
            "category": "discovery",
            "artist": r["artist"],
            "reasoning": r.get("reasoning"),
            "detail": None,
        }
        for r in results
        if r.get("artist")
        and r["artist"].strip().lower() not in existing
        and r["artist"].strip().lower() not in already_suggested
    ]


def refresh_recommendations(conn: sqlite3.Connection) -> list[dict]:
    """Returns the new recommendation rows (not yet written - see db.add_recommendation_run).
    Empty list, logged and skipped, if no API key is configured."""
    if not settings.anthropic_api_key:
        log.info("ANTHROPIC_API_KEY not configured - skipping recommendations")
        return []

    loved = top_loved_artists(conn)
    if not loved:
        log.info("Not enough play history yet to build a taste profile - skipping")
        return []

    items = find_gap_fill(conn, loved) + find_discovery(conn, loved)
    log.info(
        "Recommendations: %d gap-fill, %d discovery",
        sum(1 for i in items if i["category"] == "gap_fill"),
        sum(1 for i in items if i["category"] == "discovery"),
    )
    return items
