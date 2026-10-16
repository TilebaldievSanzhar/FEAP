"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from feap.api.auth_router import router as auth_router
from feap.api.calc_router import router as calc_router
from feap.api.dt_router import router as dt_router
from feap.api.jobs_router import router as jobs_router
from feap.api.ocr_router import router as ocr_router
from feap.api.refdata_router import router as refdata_router
from feap.api.router import router
from feap.auth.access import RedactQueryTokenFilter
from feap.config import settings
from feap.llm.client import get_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("uvicorn.access").addFilter(RedactQueryTokenFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize / tear down arq pool and redis client for the API process.

    The pool is shared by every request that submits async batches; the
    standalone redis client is what the SSE endpoint subscribes to. Both
    are skipped when JOBS_BACKEND=inprocess (local dev).
    """
    app.state.arq_pool = None
    app.state.redis_client = None

    if settings.JOBS_BACKEND == "arq":
        try:
            from arq import create_pool
            from arq.connections import RedisSettings
            import redis.asyncio as redis_asyncio

            app.state.arq_pool = await create_pool(
                RedisSettings.from_dsn(settings.REDIS_URL)
            )
            app.state.redis_client = redis_asyncio.from_url(
                settings.REDIS_URL, decode_responses=False
            )
            # Wire distributed rate limiter into the LLM provider.
            if settings.LLM_RATE_LIMITER_BACKEND == "redis":
                provider = get_provider()
                if hasattr(provider, "configure_distributed_limit"):
                    provider.configure_distributed_limit(app.state.redis_client)
            logger.info("Connected to Redis at %s", settings.REDIS_URL)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to initialize arq/redis: %s", exc)
            # Surface the error so it's not silently degraded — but don't
            # block app startup so /classify (single product) still works.

    try:
        yield
    finally:
        if app.state.arq_pool is not None:
            await app.state.arq_pool.close()
        if app.state.redis_client is not None:
            await app.state.redis_client.aclose()


app = FastAPI(
    title="FEAP — ТН ВЭД Classifier",
    description="Cascading LLM classifier for ЕАЭС customs tariff codes",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)
app.include_router(auth_router)
app.include_router(jobs_router)
app.include_router(ocr_router)
app.include_router(dt_router)
app.include_router(calc_router)
app.include_router(refdata_router)


@app.get("/health")
async def health():
    return {"status": "ok", "jobs_backend": settings.JOBS_BACKEND}
