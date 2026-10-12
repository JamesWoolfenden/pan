from __future__ import annotations

import json
import logging

import anthropic

from backend.config import settings
from backend.llm_json import strip_code_fence

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-5-5"

PROMPT = """Translate this music search query into a JSON filter object, plus a short
"semantic_query" string capturing the remaining vibe/similarity intent (drop anything already
captured as a hard filter). Schema (omit fields that don't apply, use null/empty where unknown):

{{
  "genre_include": [string, ...],
  "genre_exclude": [string, ...],
  "mood_include": [string, ...],
  "mood_exclude": [string, ...],
  "artist_exclude": [string, ...],
  "year_min": int or null,
  "year_max": int or null,
  "semantic_query": string
}}

Respond with ONLY the JSON object, no other text.

Query: {query}
"""


def parse_query(query: str) -> dict:
    """Parse a natural-language query into a structured filter via the Claude API. Falls back
    to a filter-less pass-through (pure semantic search on the original query) if no API key
    is configured or the response can't be parsed."""
    fallback = {"semantic_query": query}
    if not settings.anthropic_api_key:
        log.info("ANTHROPIC_API_KEY not configured - falling back to plain semantic search")
        return fallback

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model=MODEL,
        max_tokens=512,
        thinking={"type": "between_tools"},
        messages=[{"role": "user", "content": PROMPT.format(query=query)}],
    )
    text = "".join(block.text for block in message.content if block.type == "text")
    try:
        parsed = json.loads(strip_code_fence(text))
    except json.JSONDecodeError:
        log.warning("Could not parse cloud query filter response: %s", text[:500])
        return fallback

    parsed.setdefault("semantic_query", query)
    return parsed
