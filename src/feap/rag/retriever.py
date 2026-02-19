"""RAG retriever: find relevant explanatory notes via pgvector."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from feap.db.queries import search_explanatory_notes
from feap.rag.embedder import embed_query


async def retrieve_explanations(
    session: AsyncSession,
    product_description: str,
    code_prefix: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Retrieve relevant explanatory note chunks for a product."""
    embedding = await embed_query(product_description)
    chunks = await search_explanatory_notes(
        session, embedding, code_prefix=code_prefix, limit=limit
    )
    return [
        {
            "code": c.code,
            "section_type": c.section_type,
            "chunk_text": c.chunk_text,
        }
        for c in chunks
    ]
