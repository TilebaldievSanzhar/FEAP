"""arq task functions executed by the worker process.

Two-step flow for batch classification:

1. `run_classify_batch(job_id)` — fan-out: load all pending item ids
   for the job and enqueue a `run_classify_item` task for each. arq
   distributes them across workers up to `WORKER_MAX_JOBS` per worker.

2. `run_classify_item(item_id)` — atomic unit of work for a single
   classification. Idempotent: re-runs check item.status before doing
   anything, so a duplicate enqueue (or arq retry) is harmless.

OCR-derived flows (`run_ocr_job`, `run_ocr_split_and_enqueue`) are
declared as stubs so the worker registers them; they raise
NotImplementedError until the OCR module lands.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from feap.cascade.orchestrator import CascadeOrchestrator, SessionNotFound
from feap.jobs import repository as repo
from feap.jobs.events import publish
from feap.schemas.api import ClassifyResponse

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _serialize_response(response: ClassifyResponse) -> dict[str, Any]:
    return json.loads(response.model_dump_json())


async def _publish_event(ctx: dict, job_id: uuid.UUID, event: dict) -> None:
    redis = ctx.get("redis_client")
    await publish(redis, job_id, event)


async def _orchestrate(ctx: dict, **kwargs: Any) -> ClassifyResponse:
    """Run the cascade for one item using the orchestrator from worker context."""
    orchestrator: CascadeOrchestrator = ctx["orchestrator"]
    session_factory = ctx["db_session_factory"]
    async with session_factory() as db:
        return await orchestrator.classify(db=db, **kwargs)


async def _resume(ctx: dict, *, session_id: str, answers: dict[str, str]) -> ClassifyResponse:
    orchestrator: CascadeOrchestrator = ctx["orchestrator"]
    session_factory = ctx["db_session_factory"]
    async with session_factory() as db:
        return await orchestrator.resume(
            session_id=session_id, answers=answers, db=db
        )


# ── classify_batch fan-out ───────────────────────────────────────────────────

async def run_classify_batch(ctx: dict, job_id_str: str) -> None:
    """Pick up a job from the queue and fan out per-item tasks."""
    job_id = uuid.UUID(job_id_str)
    redis_arq = ctx["redis"]
    session_factory = ctx["db_session_factory"]

    async with session_factory() as db:
        await repo.mark_job_running(db, job_id)
        item_ids = await repo.list_pending_item_ids(db, job_id)
        await repo.log_event(
            db, job_id=job_id, event_type="started",
            payload={"total_items": len(item_ids)},
        )

    await _publish_event(ctx, job_id, {"event": "started", "total_items": len(item_ids)})

    # Fan out one arq job per item. The `_job_id=item:{id}` token is arq's
    # idempotency key — duplicate enqueues with the same token are coalesced.
    for item_id in item_ids:
        await redis_arq.enqueue_job(
            "run_classify_item",
            str(item_id),
            _job_id=f"item:{item_id}",
        )


# ── single item ─────────────────────────────────────────────────────────────

async def run_classify_item(ctx: dict, item_id_str: str) -> None:
    """Run the cascade for one item and persist the outcome."""
    item_id = uuid.UUID(item_id_str)
    session_factory = ctx["db_session_factory"]

    async with session_factory() as db:
        item = await repo.get_item(db, item_id)
        if item is None:
            logger.warning("Item %s vanished before processing", item_id)
            return
        if item.status != "pending":
            logger.info(
                "Item %s already in status=%s; skipping (idempotent guard)",
                item_id, item.status,
            )
            return

        # Honour an explicit cancel: if the parent job was cancelled while
        # this item was sitting in the queue, treat the cancellation as
        # final and don't run the cascade.
        job = await repo.get_job(db, item.job_id)
        if job is not None and job.status == "cancelled":
            logger.info("Job %s cancelled; skipping item %s", item.job_id, item_id)
            return

        claimed = await repo.mark_item_running(db, item_id)
        if not claimed:
            return

        # Snapshot the fields we need outside the session.
        job_id = item.job_id
        description = item.product_description
        additional_info = item.additional_info or None

    try:
        response = await _orchestrate(
            ctx, description=description, additional_info=additional_info
        )
    except Exception as exc:  # noqa: BLE001 — terminal item failure
        logger.exception("Cascade crashed for item %s", item_id)
        async with session_factory() as db:
            await repo.save_item_failed(db, item_id, error=f"{type(exc).__name__}: {exc}")
            new_status, _counts = await repo.update_job_aggregates(db, job_id)
            await repo.log_event(
                db, job_id=job_id, item_id=item_id,
                event_type="item_failed", payload={"error": str(exc)},
            )
        await _publish_event(
            ctx, job_id,
            {"event": "item_failed", "item_id": str(item_id), "error": str(exc)},
        )
        await _maybe_publish_finished(ctx, job_id, new_status)
        return

    await _persist_response(ctx, item_id=item_id, job_id=job_id, response=response)


async def _persist_response(
    ctx: dict,
    *,
    item_id: uuid.UUID,
    job_id: uuid.UUID,
    response: ClassifyResponse,
) -> None:
    """Translate a ClassifyResponse into item state + events + aggregates."""
    session_factory = ctx["db_session_factory"]
    payload = _serialize_response(response)

    async with session_factory() as db:
        if response.status == "completed" and response.result is not None:
            await repo.save_item_completed(
                db, item_id,
                result_json=payload["result"],
                session_id=None,
            )
            event_name = "item_done"
            event_payload = {
                "item_id": str(item_id),
                "status": "completed",
                "final_code": response.result.final_code,
            }
        elif response.status == "needs_input" and response.partial is not None:
            await repo.save_item_needs_input(
                db, item_id,
                partial_json=payload["partial"],
                session_id=response.session_id,
            )
            event_name = "item_needs_input"
            event_payload = {
                "item_id": str(item_id),
                "status": "needs_input",
                "session_id": response.session_id,
                "questions": response.partial.questions,
            }
        else:
            error = response.error or "Cascade returned no result"
            await repo.save_item_failed(db, item_id, error=error)
            event_name = "item_failed"
            event_payload = {
                "item_id": str(item_id),
                "status": "failed",
                "error": error,
            }

        new_status, _counts = await repo.update_job_aggregates(db, job_id)
        await repo.log_event(
            db, job_id=job_id, item_id=item_id,
            event_type=event_name, payload=event_payload,
        )

    await _publish_event(ctx, job_id, {"event": event_name, **event_payload})
    await _maybe_publish_finished(ctx, job_id, new_status)


async def _maybe_publish_finished(
    ctx: dict, job_id: uuid.UUID, new_status: str
) -> None:
    if new_status not in ("completed", "needs_input", "partial", "failed"):
        return
    session_factory = ctx["db_session_factory"]
    async with session_factory() as db:
        counts = await repo.recount_items(db, job_id)
        await repo.log_event(
            db, job_id=job_id, event_type="finished",
            payload={"status": new_status, "counts": counts},
        )
    await _publish_event(
        ctx, job_id,
        {"event": "finished", "status": new_status, "counts": counts},
    )


# ── needs_input resume (per item) ────────────────────────────────────────────

async def run_resume_item(
    ctx: dict, item_id_str: str, session_id: str, answers_json: str
) -> None:
    """Resume one needs_input item with user answers.

    Enqueued from the answer endpoint when the caller wants the resume to
    happen in the worker rather than inline. Inline resume is the default
    path (fast feedback for the user); this exists for batched answers
    where we want to avoid blocking the HTTP request on N cascades.
    """
    item_id = uuid.UUID(item_id_str)
    answers = json.loads(answers_json)
    session_factory = ctx["db_session_factory"]

    async with session_factory() as db:
        item = await repo.get_item(db, item_id)
        if item is None or item.status != "needs_input":
            return
        job_id = item.job_id

    try:
        response = await _resume(ctx, session_id=session_id, answers=answers)
    except SessionNotFound as exc:
        async with session_factory() as db:
            await repo.save_item_failed(db, item_id, error=str(exc))
            new_status, _ = await repo.update_job_aggregates(db, job_id)
        await _publish_event(
            ctx, job_id,
            {"event": "item_failed", "item_id": str(item_id), "error": str(exc)},
        )
        await _maybe_publish_finished(ctx, job_id, new_status)
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Resume crashed for item %s", item_id)
        async with session_factory() as db:
            await repo.save_item_failed(db, item_id, error=f"{type(exc).__name__}: {exc}")
            new_status, _ = await repo.update_job_aggregates(db, job_id)
        await _publish_event(
            ctx, job_id,
            {"event": "item_failed", "item_id": str(item_id), "error": str(exc)},
        )
        await _maybe_publish_finished(ctx, job_id, new_status)
        return

    await _persist_response(ctx, item_id=item_id, job_id=job_id, response=response)


# ── OCR stubs (implemented when the OCR module lands) ────────────────────────

async def run_ocr_job(ctx: dict, job_id_str: str) -> None:
    raise NotImplementedError(
        "OCR jobs are defined in docs/ocr_integration.md but not yet implemented."
    )


async def run_ocr_split_and_enqueue(ctx: dict, ocr_job_id_str: str) -> None:
    raise NotImplementedError(
        "OCR→classify chaining lands together with the OCR module."
    )


# ── Cron functions ───────────────────────────────────────────────────────────

async def cleanup_expired_jobs(ctx: dict) -> int:
    session_factory = ctx["db_session_factory"]
    async with session_factory() as db:
        deleted = await repo.delete_expired_jobs(db)
    if deleted:
        logger.info("Cleaned up %d expired jobs", deleted)
    return deleted


async def cleanup_old_events(ctx: dict) -> int:
    from feap.config import settings

    session_factory = ctx["db_session_factory"]
    async with session_factory() as db:
        deleted = await repo.delete_old_events(
            db, retention_hours=settings.JOB_EVENTS_RETENTION_HOURS
        )
    if deleted:
        logger.info("Cleaned up %d old job events", deleted)
    return deleted


async def reset_stuck_items(ctx: dict) -> int:
    from feap.config import settings

    session_factory = ctx["db_session_factory"]
    async with session_factory() as db:
        reset = await repo.reset_stuck_items(
            db, after_seconds=settings.STUCK_ITEMS_RESET_AFTER_S
        )
    if reset:
        logger.warning("Reset %d stuck items back to pending", reset)
    return reset
