from __future__ import annotations

import sqlite3
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from backend.config import settings
from backend.db import upsert_embedding


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    return SentenceTransformer(settings.embedding_model)


def track_text(row: sqlite3.Row) -> str:
    """Text used to build this track's search/similarity embedding. Deliberately excludes
    title: literal word overlap between unrelated songs' titles (e.g. two different tracks that
    happen to both contain "Feel") is noise for "what does this sound like" purposes, not
    signal - it was dominating "Similar" results over genuinely similar genre/mood tracks.
    Genre is repeated to weight it more heavily, since it's the most meaningful field here.
    Tempo/energy/key/year are NOT folded in here as fuzzy text buckets - backend/search/engine.py
    uses the real numeric values directly for those, as explicit weighted factors alongside this
    text embedding's distance, which is more precise than hoping a text model captures them."""
    genre = row["genre"]
    tags = (row["tags"] or "").replace(",", " ")
    parts = [row["artist"], row["album"], genre, genre, row["mood"], tags]
    return " ".join(p for p in parts if p)


def embed_text(text: str) -> list[float]:
    return get_model().encode(text, normalize_embeddings=True).tolist()


def reindex_embeddings(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> int:
    model = get_model()
    texts = [track_text(row) for row in rows]
    vectors = model.encode(
        texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False
    )
    for row, vector in zip(rows, vectors):
        upsert_embedding(conn, row["id"], vector.tolist())
    conn.commit()
    return len(rows)
