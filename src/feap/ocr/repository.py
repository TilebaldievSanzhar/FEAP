"""Postgres CRUD for ocr_jobs.

Mirrors the style of feap.jobs.repository — all writes funnel through
here so task/HTTP code deals with domain objects, not raw SQL.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from feap.config import settings
from feap.db.models import OcrJob


async def create_job(
    db: AsyncSession,
    *,
    files: list[dict],
    auto_classify: bool,
    params: dict | None = None,
) -> OcrJob:
    now = dt.datetime.now(dt.timezone.utc)
    expires_at = now + dt.timedelta(hours=settings.OCR_RESULT_TTL_HOURS)
    job = OcrJob(
        status="pending",
        stage="pending",
        files=files,
        auto_classify=auto_classify,
        params=params or {},
        expires_at=expires_at,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def get_job(db: AsyncSession, ocr_job_id: uuid.UUID) -> OcrJob | None:
    result = await db.execute(select(OcrJob).where(OcrJob.id == ocr_job_id))
    return result.scalar_one_or_none()


async def mark_running(
    db: AsyncSession, ocr_job_id: uuid.UUID, *, stage: str
) -> bool:
    """Transition pending → extracting (or structuring). Returns True on success."""
    now = dt.datetime.now(dt.timezone.utc)
    result = await db.execute(
        update(OcrJob)
        .where(
            OcrJob.id == ocr_job_id,
            OcrJob.status.in_(("pending", "extracting", "structuring")),
        )
        .values(
            status=stage,
            stage=stage,
            started_at=func.coalesce(OcrJob.started_at, now),
        )
    )
    await db.commit()
    return (result.rowcount or 0) == 1


async def save_extraction(
    db: AsyncSession,
    ocr_job_id: uuid.UUID,
    *,
    extraction: list[dict],
) -> None:
    await db.execute(
        update(OcrJob)
        .where(OcrJob.id == ocr_job_id)
        .values(extraction=extraction)
    )
    await db.commit()


async def save_completed(
    db: AsyncSession,
    ocr_job_id: uuid.UUID,
    *,
    products: list[dict],
    skipped_notes: list[str] | None = None,
    linked_classification_job_id: uuid.UUID | None = None,
) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    await db.execute(
        update(OcrJob)
        .where(OcrJob.id == ocr_job_id)
        .values(
            status="completed",
            stage="completed",
            products=products,
            skipped_notes=skipped_notes,
            linked_classification_job_id=linked_classification_job_id,
            completed_at=now,
        )
    )
    await db.commit()


async def link_classification_job(
    db: AsyncSession,
    ocr_job_id: uuid.UUID,
    *,
    classification_job_id: uuid.UUID,
) -> None:
    await db.execute(
        update(OcrJob)
        .where(OcrJob.id == ocr_job_id)
        .values(linked_classification_job_id=classification_job_id)
    )
    await db.commit()


async def save_failed(
    db: AsyncSession, ocr_job_id: uuid.UUID, *, error: str
) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    await db.execute(
        update(OcrJob)
        .where(OcrJob.id == ocr_job_id)
        .values(status="failed", stage="failed", error=error, completed_at=now)
    )
    await db.commit()


async def cancel_job(db: AsyncSession, ocr_job_id: uuid.UUID) -> OcrJob | None:
    """Mark not-yet-terminal job as cancelled. Returns the refreshed job."""
    job = await get_job(db, ocr_job_id)
    if job is None:
        return None
    if job.status in ("completed", "failed", "cancelled"):
        return job
    now = dt.datetime.now(dt.timezone.utc)
    await db.execute(
        update(OcrJob)
        .where(OcrJob.id == ocr_job_id)
        .values(status="cancelled", stage="cancelled", completed_at=now)
    )
    await db.commit()
    await db.refresh(job)
    return job


async def delete_expired(db: AsyncSession) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    result = await db.execute(delete(OcrJob).where(OcrJob.expires_at < now))
    await db.commit()
    return result.rowcount or 0
