"""High-level enqueue interface called from FastAPI handlers.

Two backends:
- ArqBackend: production. Enqueues `run_classify_batch` into Redis.
- InProcessBackend: local-dev fallback (JOBS_BACKEND=inprocess). Runs
  the same task functions in background asyncio tasks within the
  FastAPI process. Same API contract; SSE pub/sub works against an
  in-memory event bus rather than Redis.

The right backend is selected by `feap.config.settings.JOBS_BACKEND`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Protocol

from feap.config import settings

logger = logging.getLogger(__name__)


class JobBackend(Protocol):
    async def enqueue_batch(self, job_id: uuid.UUID) -> None: ...
    async def enqueue_resume_item(
        self, item_id: uuid.UUID, session_id: str, answers: dict[str, str]
    ) -> None: ...
    async def enqueue_ocr_job(self, ocr_job_id: uuid.UUID) -> None: ...


class ArqBackend:
    """Production backend: hands work off to arq workers via Redis."""

    def __init__(self, arq_pool: Any) -> None:
        self._pool = arq_pool

    async def enqueue_batch(self, job_id: uuid.UUID) -> None:
        await self._pool.enqueue_job(
            "run_classify_batch", str(job_id), _job_id=f"batch:{job_id}"
        )

    async def enqueue_resume_item(
        self, item_id: uuid.UUID, session_id: str, answers: dict[str, str]
    ) -> None:
        import json

        await self._pool.enqueue_job(
            "run_resume_item",
            str(item_id),
            session_id,
            json.dumps(answers, ensure_ascii=False),
            _job_id=f"resume:{item_id}",
        )

    async def enqueue_ocr_job(self, ocr_job_id: uuid.UUID) -> None:
        await self._pool.enqueue_job(
            "run_ocr_job", str(ocr_job_id), _job_id=f"ocr:{ocr_job_id}"
        )


class InProcessBackend:
    """Local-dev backend: runs tasks as asyncio background tasks.

    Not safe for production (no persistence beyond the running process,
    no shared queue across replicas, no recovery). Provided so developers
    can smoke-test the batch flow without booting Redis.
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._redis_client = redis_client  # may be None

    def _build_ctx(self) -> dict[str, Any]:
        from feap.cascade.orchestrator import CascadeOrchestrator
        from feap.db.engine import async_session

        return {
            "db_session_factory": async_session,
            "orchestrator": CascadeOrchestrator(),
            "redis": _SyntheticArqRedis(self),
            "redis_client": self._redis_client,
        }

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def enqueue_batch(self, job_id: uuid.UUID) -> None:
        from feap.jobs.tasks import run_classify_batch

        self._spawn(run_classify_batch(self._build_ctx(), str(job_id)))

    async def enqueue_resume_item(
        self, item_id: uuid.UUID, session_id: str, answers: dict[str, str]
    ) -> None:
        import json

        from feap.jobs.tasks import run_resume_item

        self._spawn(
            run_resume_item(
                self._build_ctx(),
                str(item_id),
                session_id,
                json.dumps(answers, ensure_ascii=False),
            )
        )

    async def enqueue_ocr_job(self, ocr_job_id: uuid.UUID) -> None:
        from feap.jobs.tasks import run_ocr_job

        self._spawn(run_ocr_job(self._build_ctx(), str(ocr_job_id)))

    async def _spawn_named(self, name: str, *args: Any) -> None:
        """Internal: re-enqueue a named task (called by SyntheticArqRedis)."""
        from feap.jobs import tasks as t

        fn = {
            "run_classify_batch": t.run_classify_batch,
            "run_classify_item": t.run_classify_item,
            "run_resume_item": t.run_resume_item,
            "run_ocr_job": t.run_ocr_job,
            "run_ocr_split_and_enqueue": t.run_ocr_split_and_enqueue,
        }.get(name)
        if fn is None:
            logger.error("InProcessBackend: unknown task name %s", name)
            return
        self._spawn(fn(self._build_ctx(), *args))


class _SyntheticArqRedis:
    """Mimics the subset of arq's redis pool that tasks use.

    Only `enqueue_job(name, *args, _job_id=...)` is needed — fan-out from
    `run_classify_batch` to `run_classify_item`.
    """

    def __init__(self, backend: InProcessBackend) -> None:
        self._backend = backend

    async def enqueue_job(self, name: str, *args: Any, **_kwargs: Any) -> None:
        await self._backend._spawn_named(name, *args)


# ── Singleton selection ──────────────────────────────────────────────────────

_inprocess_singleton: InProcessBackend | None = None


def get_inprocess_backend() -> InProcessBackend:
    global _inprocess_singleton
    if _inprocess_singleton is None:
        _inprocess_singleton = InProcessBackend()
    return _inprocess_singleton


def is_inprocess() -> bool:
    return settings.JOBS_BACKEND == "inprocess"
