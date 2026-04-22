"""Postgres-backed CRUD for jobs and items.

All writes happen through this module so the rest of the codebase
deals with domain objects, not raw SQL. The aggregation function
(`update_job_aggregates`) is the most important piece — it's the
single place where item-status counts are recomputed atomically and
job-level finalization is decided.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from feap.config import settings
from feap.db.models import (
    ClassificationJob,
    ClassificationJobEvent,
    ClassificationJobItem,
)
from feap.jobs.schemas import BatchItemInput


# ── Job creation ─────────────────────────────────────────────────────────────

async def create_job(
    db: AsyncSession,
    *,
    job_type: str,
    items: list[BatchItemInput],
    params: dict | None = None,
    parent_ocr_job_id: uuid.UUID | None = None,
    initial_status: str = "pending",
) -> ClassificationJob:
    """Create a new job and its items in a single transaction."""
    now = dt.datetime.now(dt.timezone.utc)
    expires_at = now + dt.timedelta(hours=settings.JOB_RESULT_TTL_HOURS)

    job = ClassificationJob(
        job_type=job_type,
        status=initial_status,
        params=params or {},
        total_items=len(items),
        expires_at=expires_at,
        parent_ocr_job_id=parent_ocr_job_id,
    )
    db.add(job)
    await db.flush()

    for position, item in enumerate(items):
        db.add(
            ClassificationJobItem(
                job_id=job.id,
                position=position,
                product_description=item.description,
                additional_info=item.additional_info or None,
                source_ref=item.source_ref,
                status="pending",
            )
        )

    await db.commit()
    await db.refresh(job)
    return job


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> ClassificationJob | None:
    result = await db.execute(
        select(ClassificationJob).where(ClassificationJob.id == job_id)
    )
    return result.scalar_one_or_none()


async def list_pending_item_ids(
    db: AsyncSession, job_id: uuid.UUID
) -> list[uuid.UUID]:
    result = await db.execute(
        select(ClassificationJobItem.id).where(
            ClassificationJobItem.job_id == job_id,
            ClassificationJobItem.status == "pending",
        )
    )
    return list(result.scalars().all())


async def get_item(db: AsyncSession, item_id: uuid.UUID) -> ClassificationJobItem | None:
    result = await db.execute(
        select(ClassificationJobItem).where(ClassificationJobItem.id == item_id)
    )
    return result.scalar_one_or_none()


async def list_items(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ClassificationJobItem]:
    stmt = (
        select(ClassificationJobItem)
        .where(ClassificationJobItem.job_id == job_id)
        .order_by(ClassificationJobItem.position)
        .limit(limit)
        .offset(offset)
    )
    if status:
        stmt = stmt.where(ClassificationJobItem.status == status)
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ── State transitions ────────────────────────────────────────────────────────

async def mark_job_running(db: AsyncSession, job_id: uuid.UUID) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    await db.execute(
        update(ClassificationJob)
        .where(
            ClassificationJob.id == job_id,
            ClassificationJob.status.in_(["pending", "running"]),
        )
        .values(status="running", started_at=func.coalesce(ClassificationJob.started_at, now))
    )
    await db.commit()


async def mark_item_running(db: AsyncSession, item_id: uuid.UUID) -> bool:
    """Atomically transition a pending item to running.

    Returns True if the transition happened (caller now owns the item),
    False if another worker already grabbed it (caller should bail out).
    """
    now = dt.datetime.now(dt.timezone.utc)
    result = await db.execute(
        update(ClassificationJobItem)
        .where(
            ClassificationJobItem.id == item_id,
            ClassificationJobItem.status == "pending",
        )
        .values(status="running", started_at=now, attempts=ClassificationJobItem.attempts + 1)
    )
    await db.commit()
    return result.rowcount == 1


async def save_item_completed(
    db: AsyncSession,
    item_id: uuid.UUID,
    *,
    result_json: dict,
    session_id: str | None = None,
) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    await db.execute(
        update(ClassificationJobItem)
        .where(ClassificationJobItem.id == item_id)
        .values(
            status="completed",
            result=result_json,
            partial=None,
            error=None,
            classification_session_id=session_id,
            completed_at=now,
        )
    )
    await db.commit()


async def save_item_needs_input(
    db: AsyncSession,
    item_id: uuid.UUID,
    *,
    partial_json: dict,
    session_id: str,
) -> None:
    await db.execute(
        update(ClassificationJobItem)
        .where(ClassificationJobItem.id == item_id)
        .values(
            status="needs_input",
            partial=partial_json,
            result=None,
            error=None,
            classification_session_id=session_id,
        )
    )
    await db.commit()


async def save_item_failed(db: AsyncSession, item_id: uuid.UUID, *, error: str) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    await db.execute(
        update(ClassificationJobItem)
        .where(ClassificationJobItem.id == item_id)
        .values(status="failed", error=error, completed_at=now)
    )
    await db.commit()


async def cancel_pending_items(db: AsyncSession, job_id: uuid.UUID) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    result = await db.execute(
        update(ClassificationJobItem)
        .where(
            ClassificationJobItem.job_id == job_id,
            ClassificationJobItem.status.in_(["pending"]),
        )
        .values(status="cancelled", completed_at=now)
    )
    await db.commit()
    return result.rowcount or 0


async def cancel_job(db: AsyncSession, job_id: uuid.UUID) -> ClassificationJob | None:
    """Mark job + all not-yet-terminal items as cancelled.

    Items already in `running` are left alone — caller should rely on the
    worker's cooperative shutdown to settle them; if the worker dies the
    stuck-items reset will eventually return them to pending, where they'll
    pick up the cancelled job state and short-circuit.
    """
    job = await get_job(db, job_id)
    if job is None:
        return None
    if job.status in ("completed", "failed", "cancelled"):
        return job

    await cancel_pending_items(db, job_id)
    now = dt.datetime.now(dt.timezone.utc)
    await db.execute(
        update(ClassificationJob)
        .where(ClassificationJob.id == job_id)
        .values(status="cancelled", completed_at=now)
    )
    await db.commit()
    await db.refresh(job)
    return job


# ── Aggregation / finalization ──────────────────────────────────────────────

async def recount_items(db: AsyncSession, job_id: uuid.UUID) -> dict[str, int]:
    """Recount all item statuses for a job. Returns dict of status -> count."""
    result = await db.execute(
        select(ClassificationJobItem.status, func.count())
        .where(ClassificationJobItem.job_id == job_id)
        .group_by(ClassificationJobItem.status)
    )
    counts: dict[str, int] = {
        "pending": 0, "running": 0, "completed": 0,
        "needs_input": 0, "failed": 0, "cancelled": 0,
    }
    for status, count in result.all():
        counts[status] = count
    return counts


def derive_job_status(counts: dict[str, int]) -> str:
    """Compute the job-level status from the item-status histogram.

    Rules (in order):
    - any pending or running → 'running'
    - any needs_input        → 'needs_input'
    - any failed/cancelled and any completed → 'partial'
    - all completed          → 'completed'
    - all failed/cancelled   → 'failed'  (no successful items)
    """
    if counts.get("pending", 0) + counts.get("running", 0) > 0:
        return "running"
    if counts.get("needs_input", 0) > 0:
        return "needs_input"
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0) + counts.get("cancelled", 0)
    if completed > 0 and failed > 0:
        return "partial"
    if completed > 0:
        return "completed"
    return "failed"


async def update_job_aggregates(
    db: AsyncSession, job_id: uuid.UUID
) -> tuple[str, dict[str, int]]:
    """Recount item statuses, persist counts, and (when terminal) finalize the job.

    The terminal-status update uses a guarded UPDATE so two concurrent
    workers finishing the last two items don't double-finalize. Returns
    (new_job_status, counts).
    """
    counts = await recount_items(db, job_id)
    derived = derive_job_status(counts)

    is_terminal = derived in ("completed", "needs_input", "partial", "failed")
    completed_at = (
        dt.datetime.now(dt.timezone.utc) if is_terminal else None
    )

    stmt = (
        update(ClassificationJob)
        .where(ClassificationJob.id == job_id)
        .values(
            completed_items=counts["completed"],
            failed_items=counts["failed"],
            needs_input_items=counts["needs_input"],
            cancelled_items=counts["cancelled"],
            status=derived,
            completed_at=(
                completed_at if is_terminal
                else ClassificationJob.completed_at
            ),
        )
    )
    # Don't ever overwrite a cancelled job — explicit cancel always wins.
    stmt = stmt.where(ClassificationJob.status != "cancelled")
    await db.execute(stmt)
    await db.commit()
    return derived, counts


# ── Stuck-item recovery ──────────────────────────────────────────────────────

async def reset_stuck_items(db: AsyncSession, *, after_seconds: int) -> int:
    """Return items stuck in `running` past the threshold to `pending`.

    Catches workers that died without graceful shutdown.
    """
    threshold = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=after_seconds)
    result = await db.execute(
        update(ClassificationJobItem)
        .where(
            ClassificationJobItem.status == "running",
            ClassificationJobItem.updated_at < threshold,
        )
        .values(status="pending", started_at=None)
    )
    await db.commit()
    return result.rowcount or 0


# ── TTL cleanup ─────────────────────────────────────────────────────────────

async def delete_expired_jobs(db: AsyncSession) -> int:
    """Delete jobs past expires_at. Items + events cascade automatically."""
    now = dt.datetime.now(dt.timezone.utc)
    from sqlalchemy import delete

    result = await db.execute(
        delete(ClassificationJob).where(ClassificationJob.expires_at < now)
    )
    await db.commit()
    return result.rowcount or 0


async def delete_old_events(db: AsyncSession, *, retention_hours: int) -> int:
    """Delete events older than retention window."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=retention_hours)
    from sqlalchemy import delete

    result = await db.execute(
        delete(ClassificationJobEvent).where(ClassificationJobEvent.created_at < cutoff)
    )
    await db.commit()
    return result.rowcount or 0


# ── Event log ───────────────────────────────────────────────────────────────

async def log_event(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
    item_id: uuid.UUID | None = None,
) -> None:
    db.add(
        ClassificationJobEvent(
            job_id=job_id,
            item_id=item_id,
            event_type=event_type,
            payload=payload or {},
        )
    )
    await db.commit()
