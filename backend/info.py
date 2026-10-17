from __future__ import annotations

import logging

import anthropic

from backend.config import settings

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-5-5"

PROMPT = """Write a short, interesting briefing (3-5 sentences) about this track for a music
nerd - artist background, the album's context, and any notable trivia about this specific song
if you know it. Skip generic filler; only include things actually worth knowing. If you don't
recognize the track, say so briefly rather than guessing.

Track: {title}
Artist: {artist}
Album: {album}
Year: {year}
"""


def get_track_background(row) -> str:
    """Ask Claude for a short background writeup on this track/artist/album, for a user who
    wants more context than the catalog metadata gives. Returns an empty string if no API key
    is configured."""
    if not settings.anthropic_api_key:
        return ""

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model=MODEL,
        max_tokens=600,
        # This model uses extended thinking by default, which eats a variable (sometimes large)
        # chunk of max_tokens before any visible text is produced - a tight budget can get a
        # response with reasoning but no actual answer (stop_reason "max_tokens", zero text
        # blocks). None of this app's prompts need multi-step reasoning, so turn it off.
        thinking={"type": "between_tools"},
        messages=[
            {
                "role": "user",
                "content": PROMPT.format(
                    title=row["title"] or "Unknown",
                    artist=row["artist"] or "Unknown",
                    album=row["album"] or "Unknown",
                    year=row["year"] or "Unknown",
                ),
            }
        ],
    )
    return "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()
