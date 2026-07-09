"""HTTP endpoints for the OCR job system.

Endpoints:
- POST   /api/v1/ocr/jobs            — upload 1..N files, optional auto_classify
- GET    /api/v1/ocr/jobs/{id}       — metadata + stage + result (if completed)
- DELETE /api/v1/ocr/jobs/{id}       — cancel a not-yet-terminal job

See docs/ocr_integration.md. Flow:
    upload → enqueue run_ocr_job via arq
    task extracts text per file, runs LLM structurer, persists ProductCard[]
    if auto_classify=True — chained into classification_jobs
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from feap.api.deps import get_db, get_job_backend
from feap.auth.deps import AuthContext, get_auth_context
from feap.config import settings
from feap.db.models import OcrJob
from feap.usage import record_usage
from feap.jobs.enqueue import JobBackend
from feap.ocr import repository as ocr_repo
from feap.ocr import storage
from feap.ocr.errors import OcrFileRejected
from feap.ocr.schemas import (
    OcrFileExtraction,
    OcrJobFileMeta,
    OcrJobResponse,
    OcrJobSubmitResponse,
    StructuredProduct,
)

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_allowed_mime() -> set[str]:
    return {m.strip() for m in settings.OCR_ALLOWED_MIME.split(",") if m.strip()}


def _job_to_response(job: OcrJob) -> OcrJobResponse:
    files = [
        OcrJobFileMeta(
            filename=f.get("filename", ""),
            size_bytes=int(f.get("size_bytes", 0)),
            content_type=f.get("content_type", ""),
            sha256=f.get("sha256", ""),
        )
        for f in (job.files or [])
    ]
    extraction = None
    if job.extraction:
        extraction = [OcrFileExtraction.model_validate(e) for e in job.extraction]

    products = None
    if job.products:
        products = [StructuredProduct.model_validate(p) for p in job.products]

    return OcrJobResponse(
        ocr_job_id=job.id,
        status=job.status,  # type: ignore[arg-type]
        stage=job.stage,
        auto_classify=job.auto_classify,
        files=files,
        extraction=extraction,
        products=products,
        skipped_notes=job.skipped_notes,
        linked_classification_job_id=job.linked_classification_job_id,
        error=job.error,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        expires_at=job.expires_at.isoformat(),
    )


# ── Submit ──────────────────────────────────────────────────────────────────

@router.post("/ocr/jobs", status_code=202)
async def submit_ocr_job(
    http_request: Request,
    files: list[UploadFile] = File(..., description="1..N files to OCR"),
    auto_classify: bool = Form(
        default=False,
        description="If True, chain OCR result into a classification_job automatically.",
    ),
    db: AsyncSession = Depends(get_db),
    backend: JobBackend = Depends(get_job_backend),
    ctx: AuthContext = Depends(get_auth_context),
) -> OcrJobSubmitResponse:
    if not files:
        raise HTTPException(400, "At least one file is required")
    if len(files) > settings.OCR_MAX_FILES_PER_JOB:
        raise HTTPException(
            413,
            f"Too many files: {len(files)} > limit {settings.OCR_MAX_FILES_PER_JOB}",
        )

    allowed_mime = _parse_allowed_mime()
    max_bytes = settings.OCR_MAX_FILE_MB * 1024 * 1024

    raw_files: list[tuple[str, bytes, str]] = []
    for upload in files:
        content_type = upload.content_type or "application/octet-stream"
        if allowed_mime and content_type not in allowed_mime:
            raise HTTPException(
                415,
                f"Unsupported content type '{content_type}' for '{upload.filename}'. "
                f"Allowed: {sorted(allowed_mime)}",
            )
        content = await upload.read()
        if len(content) > max_bytes:
            raise HTTPException(
                413,
                f"File '{upload.filename}' exceeds size limit "
                f"{settings.OCR_MAX_FILE_MB} MB",
            )
        if len(content) == 0:
            raise HTTPException(400, f"File '{upload.filename}' is empty")
        raw_files.append((upload.filename or "unnamed", content, content_type))

    # Persist job first with empty files list, then write files on disk under
    # <ocr_upload_dir>/<job_id>/, update files metadata. This keeps the job
    # row visible even if disk write fails later — we can then mark failed.
    job = await ocr_repo.create_job(db, files=[], auto_classify=auto_classify)
    try:
        files_meta = storage.save_files(job.id, raw_files)
    except OcrFileRejected as exc:
        await ocr_repo.save_failed(db, job.id, error=str(exc))
        raise HTTPException(413, str(exc))
    except OSError as exc:
        await ocr_repo.save_failed(db, job.id, error=f"upload failed: {exc}")
        raise HTTPException(500, f"Failed to persist uploaded files: {exc}")

    # Write file metadata back to the job row.
    from sqlalchemy import update as _update
    await db.execute(
        _update(OcrJob).where(OcrJob.id == job.id).values(files=files_meta)
    )
    await db.commit()

    try:
        await backend.enqueue_ocr_job(job.id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to enqueue OCR job %s", job.id)
        await ocr_repo.save_failed(db, job.id, error=f"enqueue failed: {exc}")
        raise HTTPException(503, f"Очередь временно недоступна: {exc}")

    record_usage(
        db, org_id=ctx.org_id, kind="ocr_document", quantity=len(files_meta),
        ref_type="ocr_job", ref_id=str(job.id),
    )
    await db.commit()

    poll_url = str(http_request.url_for("get_ocr_job", ocr_job_id=str(job.id)))
    return OcrJobSubmitResponse(
        ocr_job_id=job.id,
        status="pending",
        files_count=len(files_meta),
        auto_classify=auto_classify,
        poll_url=poll_url,
    )


# ── Inspect ─────────────────────────────────────────────────────────────────

@router.get("/ocr/jobs/{ocr_job_id}", name="get_ocr_job")
async def get_ocr_job_endpoint(
    ocr_job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> OcrJobResponse:
    job = await ocr_repo.get_job(db, ocr_job_id)
    if job is None:
        raise HTTPException(404, f"OCR job {ocr_job_id} not found")
    return _job_to_response(job)


# ── Cancel ──────────────────────────────────────────────────────────────────

@router.delete("/ocr/jobs/{ocr_job_id}")
async def cancel_ocr_job_endpoint(
    ocr_job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> OcrJobResponse:
    job = await ocr_repo.cancel_job(db, ocr_job_id)
    if job is None:
        raise HTTPException(404, f"OCR job {ocr_job_id} not found")
    return _job_to_response(job)
