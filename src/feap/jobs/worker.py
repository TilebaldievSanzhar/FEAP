"""arq WorkerSettings — entry point for the worker process.

Run with:
    arq feap.jobs.worker.WorkerSettings

The worker process imports and reuses the same orchestrator code as the
FastAPI app — see CascadeOrchestrator. Each item gets its own DB session
via the session factory placed in `ctx`. The semaphore inside
OpenRouterProvider caps in-flight LLM calls per worker process; an
optional Redis-backed limiter caps cluster-wide RPM.
"""

from __future__ import annotations

import logging
from typing import Any

from arq.connections import RedisSettings
from arq.cron import cron
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from feap.cascade.orchestrator import CascadeOrchestrator
from feap.config import settings
from feap.jobs.tasks import (
    cleanup_expired_jobs,
    cleanup_old_events,
    reset_stuck_items,
    run_classify_batch,
    run_classify_item,
    run_ocr_job,
    run_ocr_split_and_enqueue,
    run_resume_item,
)
from feap.llm.client import get_provider

logger = logging.getLogger(__name__)


async def on_startup(ctx: dict[str, Any]) -> None:
    """Initialize per-worker resources: DB engine, orchestrator, LLM client."""
    engine = create_async_engine(settings.DATABASE_URL, pool_size=10, max_overflow=20)
    ctx["db_engine"] = engine
    ctx["db_session_factory"] = async_sessionmaker(engine, expire_on_commit=False)
    ctx["orchestrator"] = CascadeOrchestrator()

    # Reuse arq's redis pool as the SSE pub/sub client and (optionally) as
    # the distributed-rate-limit backend. Using the same connection avoids
    # a second connection pool per worker.
    ctx["redis_client"] = ctx.get("redis")
    if settings.LLM_RATE_LIMITER_BACKEND == "redis" and ctx.get("redis") is not None:
        try:
            provider = get_provider()
            if hasattr(provider, "configure_distributed_limit"):
                provider.configure_distributed_limit(ctx["redis"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to wire redis rate limiter; falling back to local: %s", exc
            )

    logger.info(
        "Worker started (max_jobs=%d, llm_concurrent=%d, rpm=%s)",
        settings.WORKER_MAX_JOBS,
        settings.LLM_MAX_CONCURRENT,
        settings.LLM_RATE_LIMIT_PER_MIN or "unlimited",
    )


async def on_shutdown(ctx: dict[str, Any]) -> None:
    engine = ctx.get("db_engine")
    if engine is not None:
        await engine.dispose()
    logger.info("Worker shutdown complete")


class WorkerSettings:
    """arq's worker configuration object."""

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    functions = [
        run_classify_batch,
        run_classify_item,
        run_resume_item,
        run_ocr_job,
        run_ocr_split_and_enqueue,
    ]
    cron_jobs = [
        # Hourly: TTL cleanup. Stagger minute to avoid both running at once.
        cron(cleanup_expired_jobs, hour=None, minute=0),
        cron(cleanup_old_events, hour=None, minute=15),
        # Every 5 minutes: stuck-item recovery.
        cron(
            reset_stuck_items,
            hour=None,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
        ),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    max_jobs = settings.WORKER_MAX_JOBS
    job_timeout = settings.WORKER_JOB_TIMEOUT_S
    max_tries = settings.WORKER_MAX_TRIES
    keep_result = 0  # results live in Postgres, not redis
    health_check_interval = settings.WORKER_HEALTH_CHECK_S
