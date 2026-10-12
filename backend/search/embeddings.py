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
    parts = [row["title"], row["artist"], row["album"], row["genre"], row["mood"]]
    return " ".join(p for p in parts if p)


def embed_text(text: str) -> list[float]:
    return get_model().encode(text, normalize_embeddings=True).tolist()


def reindex_embeddings(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> int:
    model = get_model()
    texts = [track_text(row) for row in rows]
    vectors = model.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False)
    for row, vector in zip(rows, vectors):
        upsert_embedding(conn, row["id"], vector.tolist())
    conn.commit()
    return len(rows)
