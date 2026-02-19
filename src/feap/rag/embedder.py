"""Generate embeddings using Voyage AI API (via httpx, no SDK needed)."""

from __future__ import annotations

import httpx

from feap.config import settings

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {settings.VOYAGE_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
    return _client


async def embed_query(text: str) -> list[float]:
    """Embed a search query for RAG retrieval."""
    client = _get_client()
    resp = await client.post(
        VOYAGE_API_URL,
        json={
            "input": [text],
            "model": "voyage-3",
            "input_type": "query",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return data["data"][0]["embedding"]


async def embed_documents(texts: list[str], batch_size: int = 128) -> list[list[float]]:
    """Embed multiple documents for indexing."""
    client = _get_client()
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = await client.post(
            VOYAGE_API_URL,
            json={
                "input": batch,
                "model": "voyage-3",
                "input_type": "document",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        all_embeddings.extend(item["embedding"] for item in data["data"])

    return all_embeddings
