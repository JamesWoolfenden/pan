from __future__ import annotations

import json
import logging
import sqlite3

import anthropic

from backend.config import settings
from backend.db import set_tags
from backend.llm_json import strip_code_fence

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-5-5"
BATCH_SIZE = 20

PROMPT = """You are tagging music tracks with a genre and a one-or-two-word mood descriptor
(e.g. "upbeat", "melancholic", "chill", "driving", "romantic"). You are given a JSON array of
tracks identified by id, with whatever title/artist/album/existing-tag info is known. Some
fields may be missing or unreliable - use your general knowledge of the artist/track/album
where possible. Respond with ONLY a JSON array of {{"id": <id>, "genre": <string>, "mood": <string>}}
objects, one per input track, in the same order. If you genuinely cannot infer a field, use null.

Tracks:
{tracks_json}
"""


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _tag_batch(client: anthropic.Anthropic, rows: list[sqlite3.Row]) -> list[dict]:
    payload = [
        {
            "id": row["id"],
            "title": row["title"],
            "artist": row["artist"],
            "album": row["album"],
            "existing_genre": row["genre"],
            "existing_mood": row["mood"],
        }
        for row in rows
    ]
    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        # Extended thinking (default-on for this model) eats a variable chunk of max_tokens
        # before any visible output - with a tight budget that can truncate the JSON answer
        # entirely. This task needs no multi-step reasoning, so turn thinking off.
        thinking={"type": "between_tools"},
        messages=[{"role": "user", "content": PROMPT.format(tracks_json=json.dumps(payload))}],
    )
    text = "".join(block.text for block in message.content if block.type == "text")
    try:
        return json.loads(strip_code_fence(text))
    except json.JSONDecodeError:
        log.warning("Could not parse cloud tagger response: %s", text[:500])
        return []


SUGGEST_PROMPT = """A user is correcting the genre/mood tag on this music track because they
think the current one is wrong. Suggest 5 plausible genre options and 5 plausible mood options,
ranked best-first, using your knowledge of the artist/track/album. Moods are one-or-two-word
descriptors (e.g. "upbeat", "melancholic", "chill", "driving", "romantic").

Track: {track_json}

Respond with ONLY this JSON object: {{"genres": [string, ...], "moods": [string, ...]}}
"""


def suggest_tags(row: sqlite3.Row) -> dict:
    """Ask Claude for several candidate genre/mood options for one track, for a human to pick
    from when correcting a tag they believe is wrong. Returns {"genres": [...], "moods": [...]},
    empty lists if no API key is configured or the response can't be parsed."""
    empty = {"genres": [], "moods": []}
    if not settings.anthropic_api_key:
        log.info("ANTHROPIC_API_KEY not configured - cannot suggest tags")
        return empty

    track_json = json.dumps({
        "title": row["title"],
        "artist": row["artist"],
        "album": row["album"],
        "current_genre": row["genre"],
        "current_mood": row["mood"],
    })
    client = _client()
    message = client.messages.create(
        model=MODEL,
        max_tokens=512,
        thinking={"type": "between_tools"},
        messages=[{"role": "user", "content": SUGGEST_PROMPT.format(track_json=track_json)}],
    )
    text = "".join(block.text for block in message.content if block.type == "text")
    try:
        parsed = json.loads(strip_code_fence(text))
    except json.JSONDecodeError:
        log.warning("Could not parse tag suggestion response: %s", text[:500])
        return empty
    return {"genres": parsed.get("genres") or [], "moods": parsed.get("moods") or []}


def tag_with_cloud(conn: sqlite3.Connection, rows: list[sqlite3.Row], max_tracks: int | None = None) -> int:
    """Tag up to `max_tracks` rows (default: settings.cloud_tagging_max_per_run) via the
    Claude API, batched. Returns the number of tracks actually tagged."""
    if not settings.anthropic_api_key:
        log.info("ANTHROPIC_API_KEY not configured - skipping cloud tagging")
        return 0

    limit = max_tracks if max_tracks is not None else settings.cloud_tagging_max_per_run
    rows = rows[:limit]
    if not rows:
        return 0

    client = _client()
    tagged = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        by_id = {row["id"]: row for row in batch}
        results = _tag_batch(client, batch)
        for result in results:
            track_id = result.get("id")
            row = by_id.get(track_id)
            if row is None:
                continue
            genre = result.get("genre") or row["genre"]
            mood = result.get("mood") or row["mood"]
            if genre or mood:
                set_tags(conn, track_id, genre, mood, "cloud")
                tagged += 1
    conn.commit()
    return tagged
