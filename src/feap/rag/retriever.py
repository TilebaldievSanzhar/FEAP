"""RAG retriever: vector similarity search via pgvector.

NOTE: No longer used for explanatory notes (replaced by direct DB loading
in level3.py and level4.py). Kept for future use with classification
precedents and court decisions.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from feap.db.models import ExplanatoryNote
from feap.db.queries import search_explanatory_notes
from feap.rag.embedder import embed_query

logger = logging.getLogger(__name__)


async def retrieve_explanations(
    session: AsyncSession,
    product_description: str,
    code_prefix: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Retrieve relevant explanatory note chunks for a product.

    Skips the Voyage API call entirely if no explanatory notes with
    embeddings exist in the database (i.e. RAG data not loaded yet).
    """
    count_result = await session.execute(
        select(func.count()).select_from(ExplanatoryNote).where(
            ExplanatoryNote.embedding.isnot(None)
        )
    )
    if count_result.scalar() == 0:
        logger.debug("No explanatory notes with embeddings — skipping RAG")
        return []

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
