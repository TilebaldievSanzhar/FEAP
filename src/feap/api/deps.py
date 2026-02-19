"""FastAPI dependency injection."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.orchestrator import CascadeOrchestrator
from feap.db.engine import async_session


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session() as session:
        yield session


def get_orchestrator() -> CascadeOrchestrator:
    return CascadeOrchestrator()
