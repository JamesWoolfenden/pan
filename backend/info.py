from __future__ import annotations

import logging
import re

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

If Year above is "Unknown" and you are confident about the actual release year, end your
response with one extra line in exactly this format (nothing else on that line):
YEAR: <four-digit year>
Omit that line entirely if Year above is already known, or if you are not confident.
"""

# Matches the trailing "YEAR: 1998" line asked for above, so it can be stripped out of the
# stored writeup (which should read as plain prose, not end with a stray metadata line) and
# the catalog's own `year` column backfilled from it - see track_about in backend/app.py.
YEAR_LINE_RE = re.compile(r"\n*YEAR:\s*(\d{4})\s*$")


def _extract_year(text: str) -> tuple[str, int | None]:
    match = YEAR_LINE_RE.search(text)
    if not match:
        return text, None
    return text[: match.start()].rstrip(), int(match.group(1))


def get_track_background(row) -> tuple[str, int | None]:
    """Ask Claude for a short background writeup on this track/artist/album, for a user who
    wants more context than the catalog metadata gives. Returns (text, inferred_year) - the
    inferred year is non-None only when the catalog didn't already have one and Claude was
    confident enough to state it (see track_about, which backfills it). Returns ("", None) if
    no API key is configured."""
    if not settings.anthropic_api_key:
        return "", None

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
    text = "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()
    return _extract_year(text)
