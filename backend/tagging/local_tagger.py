from __future__ import annotations

import sqlite3

from backend.db import set_tags

# (max_tempo, max_energy) -> mood label, checked in order
MOOD_RULES = [
    (90, 0.35, "mellow"),
    (90, 1.01, "melancholic"),
    (120, 0.5, "chill"),
    (120, 1.01, "upbeat"),
    (1000, 0.5, "driving"),
    (1000, 1.01, "energetic"),
]


def infer_mood(tempo: float, energy: float) -> str:
    for max_tempo, max_energy, label in MOOD_RULES:
        if tempo <= max_tempo and energy <= max_energy:
            return label
    return "energetic"


def apply_local_tags(conn: sqlite3.Connection, track_id: int, genre: str | None, mood: str | None,
                      tempo: float | None, energy: float | None) -> bool:
    """Fill in mood from audio features when it's missing but genre/mood both still need
    *something*. Returns True if it set a tag, so the caller can skip the cloud tagger."""
    if genre and mood:
        return True

    new_mood = mood
    if new_mood is None and tempo is not None and energy is not None:
        new_mood = infer_mood(tempo, energy)

    if genre or new_mood:
        set_tags(conn, track_id, genre, new_mood, "local" if new_mood and not mood else "metadata")
        return bool(genre and new_mood)

    return False
