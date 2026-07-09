"""FastAPI dependency injection."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.orchestrator import CascadeOrchestrator
from feap.db.engine import async_session
from feap.jobs.enqueue import (
    ArqBackend,
    JobBackend,
    get_inprocess_backend,
    is_inprocess,
)


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session() as session:
        yield session


def get_orchestrator() -> CascadeOrchestrator:
    return CascadeOrchestrator()


# ── Job system ──────────────────────────────────────────────────────────────

def get_arq_pool(app: Any) -> Any | None:
    """Return the arq pool stored on app.state, or None if backend != arq."""
    return getattr(app.state, "arq_pool", None)


def get_redis_client(app: Any) -> Any | None:
    """Return the standalone redis client used for SSE pub/sub."""
    return getattr(app.state, "redis_client", None)


def get_job_backend(request: Request) -> JobBackend:
    """Pick the right job backend based on settings.JOBS_BACKEND."""
    if is_inprocess():
        backend = get_inprocess_backend()
        # Attach redis client (None in inprocess mode is fine — events become no-ops)
        backend._redis_client = get_redis_client(request.app)
        return backend
    pool = get_arq_pool(request.app)
    if pool is None:
        raise RuntimeError(
            "JOBS_BACKEND=arq but arq pool not initialized. "
            "Check FastAPI lifespan and REDIS_URL."
        )
    return ArqBackend(pool)
