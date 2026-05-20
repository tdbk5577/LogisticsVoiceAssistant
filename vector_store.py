"""
pgvector-backed semantic search over FMCSA regulation text.

Uses Voyage AI (voyage-3-large, 1024-dim) for embeddings.
"""

import os
import voyageai
import psycopg2
import psycopg2.extras

from database import DATABASE_URL, EMBED_DIM

_MODEL = "voyage-3-large"
_voyage = voyageai.Client(api_key=os.environ.get("VOYAGE_API_KEY"))


def _embed(texts: list[str], input_type: str) -> list[list[float]]:
    """input_type is 'document' for ingest, 'query' for search — Voyage tunes both sides."""
    result = _voyage.embed(texts, model=_MODEL, input_type=input_type)
    return result.embeddings


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def ingest(docs: list[dict]) -> int:
    """
    Embed and store documents. Each dict needs: title, citation, content.
    Replaces all existing rows (idempotent re-ingest of the corpus).
    Returns the count inserted.
    """
    if not docs:
        return 0
    contents = [d["content"] for d in docs]
    vectors = _embed(contents, input_type="document")

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE fmcsa_documents RESTART IDENTITY")
            psycopg2.extras.execute_batch(
                cur,
                "INSERT INTO fmcsa_documents (title, citation, content, embedding) "
                "VALUES (%s, %s, %s, %s::vector)",
                [
                    (d["title"], d.get("citation", ""), d["content"], _vec_literal(v))
                    for d, v in zip(docs, vectors)
                ],
            )
        conn.commit()
    finally:
        conn.close()
    return len(docs)


def search(query: str, k: int = 3) -> list[dict]:
    """Return the top-k FMCSA passages most semantically similar to `query`."""
    qvec = _embed([query], input_type="query")[0]
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT title, citation, content, "
                "       1 - (embedding <=> %s::vector) AS score "
                "FROM fmcsa_documents "
                "ORDER BY embedding <=> %s::vector "
                "LIMIT %s",
                (_vec_literal(qvec), _vec_literal(qvec), k),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
