"""Inline (sync-mode) batch runner.

Used by `POST /classify/batch` when `mode=sync` (or `mode=auto` with a
small enough batch). Runs each item through the cascade concurrently
inside the request handler — *no* job records are created. The caller
gets results in the response body.

Concurrency is bounded by the OpenRouterProvider's per-process
semaphore, so even an unrestricted gather() here can't overrun the LLM.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from feap.cascade.orchestrator import CascadeOrchestrator
from feap.db.engine import engine
from feap.jobs.schemas import BatchItemInput, BatchSyncItemResult, BatchSyncResponse
from feap.schemas.api import ClassifyResponse


async def _run_one(
    orchestrator: CascadeOrchestrator,
    session_factory: async_sessionmaker,
    position: int,
    item: BatchItemInput,
    org_id: uuid.UUID | None = None,
) -> BatchSyncItemResult:
    """Run a single item, swallowing exceptions into a `failed` result."""
    async with session_factory() as db:
        try:
            response: ClassifyResponse = await orchestrator.classify(
                description=item.description,
                additional_info=item.additional_info or None,
                db=db,
                org_id=org_id,
            )
        except Exception as exc:  # noqa: BLE001
            return BatchSyncItemResult(
                position=position, status="failed", error=f"{type(exc).__name__}: {exc}"
            )

    if response.status == "completed":
        return BatchSyncItemResult(
            position=position, status="completed", result=response.result,
            session_id=response.session_id,
        )
    if response.status == "needs_input":
        return BatchSyncItemResult(
            position=position, status="needs_input",
            session_id=response.session_id, partial=response.partial,
        )
    return BatchSyncItemResult(
        position=position, status="failed",
        session_id=response.session_id, error=response.error,
    )


async def run_batch_sync(
    items: list[BatchItemInput], org_id: uuid.UUID | None = None,
) -> BatchSyncResponse:
    """Run all items concurrently and return their per-item results.

    org_id — владелец сессий needs_input (продолжить сможет только он).
    """
    orchestrator = CascadeOrchestrator()
    # Each cascade gets its own AsyncSession; we share the engine pool.
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    coros = [
        _run_one(orchestrator, session_factory, i, it, org_id)
        for i, it in enumerate(items)
    ]
    results = await asyncio.gather(*coros)
    return BatchSyncResponse(results=results)
