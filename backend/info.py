from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import anthropic

from backend import musicbrainz, wikipedia
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

# Appended when MusicBrainz links the track's album to a Wikipedia article - real source text,
# so the briefing isn't resting on memory alone for the album's context.
REFERENCE = """
Reference - the opening of Wikipedia's article on this album. Treat it as more reliable than
your own memory where they disagree, and don't repeat it wholesale - pick out what's worth
knowing:
{summary}
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


@dataclass
class Background:
    text: str
    # Non-None only when the catalog has no year and one was found - MusicBrainz's release
    # date for the album if it matched, else Claude's own stated year (see track_about, which
    # backfills it).
    inferred_year: int | None = None
    # Sources for the reader: whichever of "wikipedia", "review", "musicbrainz" exist for this
    # exact album.
    links: dict[str, str] = field(default_factory=dict)


def _album_sources(row) -> tuple[dict, str | None, int | None]:
    """(links, wikipedia summary, MusicBrainz year) for this track's album - all empty when
    MusicBrainz has no album by that title for the artist (e.g. a compilation pan files under
    another name), in which case the briefing works exactly as it did before."""
    album = musicbrainz.find_album(row["artist"] or "", row["album"] or "")
    if album is None:
        return {}, None, None
    links = {kind: album[kind] for kind in ("wikipedia", "review") if album.get(kind)}
    if album.get("url"):
        links["musicbrainz"] = album["url"]
    summary = (
        wikipedia.article_summary(album["wikipedia"])
        if album.get("wikipedia")
        else None
    )
    return links, summary, album.get("year")


def get_track_background(row) -> Background:
    """Ask Claude for a short background writeup on this track/artist/album, for a user who
    wants more context than the catalog metadata gives - grounded in the album's Wikipedia
    article when MusicBrainz links one, and returned with links to its sources. Empty text if
    no API key is configured."""
    if not settings.anthropic_api_key:
        return Background("")

    links, summary, mb_year = _album_sources(row)
    prompt = PROMPT.format(
        title=row["title"] or "Unknown",
        artist=row["artist"] or "Unknown",
        album=row["album"] or "Unknown",
        # A real release year means Claude isn't asked to guess one.
        year=row["year"] or mb_year or "Unknown",
    )
    if summary:
        prompt += REFERENCE.format(summary=summary)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model=MODEL,
        max_tokens=600,
        # This model uses extended thinking by default, which eats a variable (sometimes large)
        # chunk of max_tokens before any visible text is produced - a tight budget can get a
        # response with reasoning but no actual answer (stop_reason "max_tokens", zero text
        # blocks). None of this app's prompts need multi-step reasoning, so turn it off.
        thinking={"type": "between_tools"},
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()
    text, claude_year = _extract_year(text)
    inferred_year = None if row["year"] else (mb_year or claude_year)
    return Background(text, inferred_year, links)
