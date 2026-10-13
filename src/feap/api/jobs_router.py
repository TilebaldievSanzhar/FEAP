"""HTTP endpoints for the async job system.

Endpoints:
- POST   /classify/batch              — submit N items, sync or async
- GET    /jobs/{id}                   — job metadata + progress
- GET    /jobs/{id}/items             — paginated item list (filterable by status)
- POST   /jobs/{id}/items/{item}/answer — answer one needs_input item
- POST   /jobs/{id}/answers           — answer many items at once
- DELETE /jobs/{id}                   — cancel a running job
- GET    /jobs/{id}/events            — SSE stream of live events

The router stays decoupled from `api/router.py` so the single-product
flow keeps working untouched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from feap.api.deps import get_db, get_job_backend
from feap.auth.access import job_owner, owner_matches
from feap.auth.deps import AuthContext, get_auth_context
from feap.cascade.orchestrator import CascadeOrchestrator, SessionNotFound
from feap.config import settings
from feap.db.models import ClassificationJob, ClassificationJobItem
from feap.jobs import repository as repo
from feap.jobs.enqueue import JobBackend
from feap.jobs.events import subscribe
from feap.jobs.schemas import (
    BatchAsyncResponse,
    ClassifyBatchRequest,
    JobAnswerRequest,
    JobAnswersBatchRequest,
    JobAnswersBatchResponse,
    JobItemAnswerResponse,
    JobItemResponse,
    JobItemsResponse,
    JobProgress,
    JobResponse,
)
from feap.jobs.sync_runner import run_batch_sync
from feap.schemas.api import ClassifyResponse
from feap.schemas.validation import ClassificationResult, PartialResult
from feap.usage import record_usage

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _owned_job(
    db: AsyncSession, job_id: uuid.UUID, ctx: AuthContext,
) -> ClassificationJob:
    """Задача текущей организации; чужая неотличима от несуществующей (404)."""
    job = await repo.get_job(db, job_id)
    if job is None or not owner_matches(job_owner(job), ctx.org_id):
        raise HTTPException(404, f"Job {job_id} not found")
    return job


def _job_to_response(job: ClassificationJob, counts: dict[str, int] | None = None) -> JobResponse:
    counts = counts or {
        "pending": max(
            0,
            job.total_items
            - job.completed_items
            - job.failed_items
            - job.needs_input_items
            - job.cancelled_items,
        ),
        "running": 0,
        "completed": job.completed_items,
        "failed": job.failed_items,
        "needs_input": job.needs_input_items,
        "cancelled": job.cancelled_items,
    }
    return JobResponse(
        job_id=job.id,
        job_type=job.job_type,
        status=job.status,
        progress=JobProgress(
            total=job.total_items,
            pending=counts.get("pending", 0),
            running=counts.get("running", 0),
            completed=counts.get("completed", 0),
            failed=counts.get("failed", 0),
            needs_input=counts.get("needs_input", 0),
            cancelled=counts.get("cancelled", 0),
        ),
        parent_ocr_job_id=job.parent_ocr_job_id,
        error=job.error,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        expires_at=job.expires_at.isoformat(),
    )


def _item_to_response(item: ClassificationJobItem) -> JobItemResponse:
    result_obj = ClassificationResult.model_validate(item.result) if item.result else None
    partial_obj = PartialResult.model_validate(item.partial) if item.partial else None
    return JobItemResponse(
        item_id=item.id,
        position=item.position,
        status=item.status,
        product_description=item.product_description,
        source_ref=item.source_ref,
        session_id=item.classification_session_id,
        result=result_obj,
        partial=partial_obj,
        error=item.error,
    )


# ── Batch submission ────────────────────────────────────────────────────────

@router.post("/classify/batch")
async def classify_batch(
    request: ClassifyBatchRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    backend: JobBackend = Depends(get_job_backend),
    ctx: AuthContext = Depends(get_auth_context),
) -> Any:
    """Submit N items for classification. Returns sync or async response based on mode."""
    n = len(request.items)
    if n > settings.BATCH_ASYNC_MAX_ITEMS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Batch size {n} exceeds limit {settings.BATCH_ASYNC_MAX_ITEMS}. "
                f"Split into multiple requests."
            ),
        )

    use_sync = (
        request.mode == "sync"
        or (request.mode == "auto" and n <= settings.BATCH_SYNC_MAX)
    )

    if request.mode == "sync" and n > settings.BATCH_SYNC_MAX:
        raise HTTPException(
            status_code=400,
            detail=(
                f"mode='sync' supports at most {settings.BATCH_SYNC_MAX} items "
                f"({n} requested). Use mode='async' or mode='auto'."
            ),
        )

    if use_sync:
        result = await run_batch_sync(request.items, org_id=ctx.org_id)
        record_usage(
            db, org_id=ctx.org_id, kind="classification", quantity=n,
            ref_type="classify_batch_sync", meta={"mode": "sync"},
        )
        await db.commit()
        return result

    # Async path: persist job + items, hand off to backend.
    job = await repo.create_job(
        db, job_type="classify_batch", items=request.items,
        params={"org_id": str(ctx.org_id)},
        org_id=ctx.org_id,
    )
    try:
        await backend.enqueue_batch(job.id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to enqueue job %s", job.id)
        raise HTTPException(
            status_code=503,
            detail=f"Очередь временно недоступна: {exc}",
        )
    record_usage(
        db, org_id=ctx.org_id, kind="classification", quantity=n,
        ref_type="classification_job", ref_id=str(job.id), meta={"mode": "async"},
    )
    await db.commit()

    base = str(http_request.url_for("get_job", job_id=str(job.id)))
    return BatchAsyncResponse(
        job_id=job.id,
        status="pending",
        total_items=n,
        poll_url=base,
        items_url=f"{base}/items",
        events_url=f"{base}/events",
    )


# ── Job inspection ──────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}", name="get_job")
async def get_job_endpoint(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> JobResponse:
    job = await _owned_job(db, job_id, ctx)
    counts = await repo.recount_items(db, job_id)
    return _job_to_response(job, counts)


@router.get("/jobs/{job_id}/items")
async def list_job_items(
    job_id: uuid.UUID,
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> JobItemsResponse:
    await _owned_job(db, job_id, ctx)
    items = await repo.list_items(db, job_id, status=status, limit=limit, offset=offset)
    next_cursor = str(offset + limit) if len(items) == limit else None
    return JobItemsResponse(
        items=[_item_to_response(it) for it in items],
        next_cursor=next_cursor,
    )


# ── Per-item answer (needs_input resume) ────────────────────────────────────

async def _answer_one_inline(
    db: AsyncSession,
    item: ClassificationJobItem,
    answers: dict[str, str],
) -> JobItemAnswerResponse:
    """Resume one item inline (in the request handler)."""
    if item.status != "needs_input":
        return JobItemAnswerResponse(
            item_id=item.id,
            status=item.status,
            error=f"Item is not awaiting input (status={item.status}).",
        )
    if item.classification_session_id is None:
        return JobItemAnswerResponse(
            item_id=item.id, status="failed",
            error="Item has no associated session.",
        )

    orchestrator = CascadeOrchestrator()
    try:
        response: ClassifyResponse = await orchestrator.resume(
            session_id=item.classification_session_id, answers=answers, db=db,
        )
    except SessionNotFound:
        await repo.save_item_failed(
            db, item.id, error="Сессия истекла; запустите классификацию заново."
        )
        await repo.update_job_aggregates(db, item.job_id)
        return JobItemAnswerResponse(
            item_id=item.id, status="failed", error="Сессия истекла.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Resume failed for item %s", item.id)
        await repo.save_item_failed(db, item.id, error=str(exc))
        await repo.update_job_aggregates(db, item.job_id)
        return JobItemAnswerResponse(
            item_id=item.id, status="failed", error=str(exc),
        )

    payload = json.loads(response.model_dump_json())
    if response.status == "completed" and response.result is not None:
        await repo.save_item_completed(db, item.id, result_json=payload["result"])
        await repo.update_job_aggregates(db, item.job_id)
        return JobItemAnswerResponse(
            item_id=item.id, status="completed", result=response.result,
        )
    if response.status == "needs_input" and response.partial is not None:
        await repo.save_item_needs_input(
            db, item.id,
            partial_json=payload["partial"],
            session_id=response.session_id,
        )
        await repo.update_job_aggregates(db, item.job_id)
        return JobItemAnswerResponse(
            item_id=item.id, status="needs_input", partial=response.partial,
        )
    error = response.error or "Unknown cascade error"
    await repo.save_item_failed(db, item.id, error=error)
    await repo.update_job_aggregates(db, item.job_id)
    return JobItemAnswerResponse(item_id=item.id, status="failed", error=error)


@router.post("/jobs/{job_id}/items/{item_id}/answer")
async def answer_job_item(
    job_id: uuid.UUID,
    item_id: uuid.UUID,
    request: JobAnswerRequest,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> JobItemAnswerResponse:
    await _owned_job(db, job_id, ctx)
    item = await repo.get_item(db, item_id)
    if item is None or item.job_id != job_id:
        raise HTTPException(404, f"Item {item_id} not found in job {job_id}")
    return await _answer_one_inline(db, item, request.answers)


@router.post("/jobs/{job_id}/answers")
async def answer_job_items_batch(
    job_id: uuid.UUID,
    request: JobAnswersBatchRequest,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> JobAnswersBatchResponse:
    await _owned_job(db, job_id, ctx)
    answered: list[JobItemAnswerResponse] = []
    for entry in request.answers:
        item = await repo.get_item(db, entry.item_id)
        if item is None or item.job_id != job_id:
            answered.append(JobItemAnswerResponse(
                item_id=entry.item_id, status="failed",
                error="Item not found in this job.",
            ))
            continue
        # Run sequentially to keep DB session simple and respect the
        # per-process LLM semaphore (which would already serialize anyway).
        answered.append(await _answer_one_inline(db, item, entry.answers))

    job = await repo.get_job(db, job_id)
    job_status = job.status if job else "failed"
    return JobAnswersBatchResponse(answered=answered, job_status=job_status)


# ── Cancellation ────────────────────────────────────────────────────────────

@router.delete("/jobs/{job_id}")
async def cancel_job_endpoint(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> JobResponse:
    await _owned_job(db, job_id, ctx)
    job = await repo.cancel_job(db, job_id)
    if job is None:
        raise HTTPException(404, f"Job {job_id} not found")
    counts = await repo.recount_items(db, job_id)
    return _job_to_response(job, counts)


# ── SSE stream ──────────────────────────────────────────────────────────────

def _format_sse(event: str, data: dict | str) -> bytes:
    body = data if isinstance(data, str) else json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {event}\ndata: {body}\n\n".encode("utf-8")


async def _sse_event_stream(
    request: Request,
    job_id: uuid.UUID,
    redis: Any,
    db_factory: Any,
):
    """Yield SSE-formatted bytes for the given job's live events."""
    # Initial snapshot so reconnecting clients pick up where they are.
    async with db_factory() as db:
        job = await repo.get_job(db, job_id)
        if job is None:
            yield _format_sse("error", {"message": f"Job {job_id} not found"})
            return
        counts = await repo.recount_items(db, job_id)
    yield _format_sse("snapshot", {"status": job.status, "counts": counts})

    if job.status in ("completed", "failed", "cancelled", "needs_input", "partial"):
        # Already terminal — emit and close. Client can poll /items.
        yield _format_sse("finished", {"status": job.status, "counts": counts})
        return

    # Live stream from Redis pub/sub interleaved with periodic heartbeats.
    if redis is None:
        # Without Redis we can only poll the DB; keep it cheap.
        async for frame in _sse_polling_fallback(request, job_id, db_factory):
            yield frame
        return

    sub_iter = subscribe(redis, job_id)
    sub_anext = sub_iter.__anext__

    async def next_event() -> dict:
        return await sub_anext()

    pending: asyncio.Task | None = None
    try:
        while True:
            if await request.is_disconnected():
                return
            if pending is None:
                pending = asyncio.create_task(next_event())
            done, _ = await asyncio.wait(
                {pending}, timeout=settings.SSE_HEARTBEAT_S
            )
            if pending in done:
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    return
                pending = None
                event_name = event.pop("event", "message")
                yield _format_sse(event_name, event)
                if event_name == "finished":
                    return
            else:
                yield _format_sse("ping", {"ts": "tick"})
    finally:
        if pending is not None and not pending.done():
            pending.cancel()


async def _sse_polling_fallback(request: Request, job_id: uuid.UUID, db_factory: Any):
    """No-Redis fallback: poll DB every few seconds and emit deltas."""
    last_status: str | None = None
    last_counts: dict[str, int] = {}
    while True:
        if await request.is_disconnected():
            return
        async with db_factory() as db:
            job = await repo.get_job(db, job_id)
            if job is None:
                yield _format_sse("error", {"message": "Job vanished"})
                return
            counts = await repo.recount_items(db, job_id)
        if job.status != last_status or counts != last_counts:
            yield _format_sse("progress", {"status": job.status, "counts": counts})
            last_status, last_counts = job.status, counts
        if job.status in ("completed", "failed", "cancelled", "needs_input", "partial"):
            yield _format_sse("finished", {"status": job.status, "counts": counts})
            return
        await asyncio.sleep(max(2, settings.SSE_HEARTBEAT_S // 3))


@router.get("/jobs/{job_id}/events")
async def stream_job_events(
    job_id: uuid.UUID,
    request: Request,
    access_token: str | None = Query(
        None, description="JWT для EventSource (браузерный EventSource не умеет заголовки)",
    ),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Server-Sent Events stream for live job progress.

    Идентичность — Bearer-заголовок или ?access_token=. С токеном (или в
    AUTH_MODE=required) владелец задачи проверяется до открытия потока;
    анонимный запрос в optional-режиме работает как раньше (dev).
    """
    from feap.api.deps import get_redis_client
    from feap.db.engine import async_session

    header = request.headers.get("authorization") or ""
    token = access_token or (header[7:].strip() if header.lower().startswith("bearer ") else None)
    if token or settings.AUTH_MODE == "required":
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token) if token else None
        ctx = await get_auth_context(db, creds, request.headers.get("X-Org-Id"))
        await _owned_job(db, job_id, ctx)
    redis = get_redis_client(request.app)
    return StreamingResponse(
        _sse_event_stream(request, job_id, redis, async_session),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering for nginx
        },
    )
