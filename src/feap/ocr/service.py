"""High-level orchestrator for the OCR pipeline.

Split deliberately from arq tasks: this module has no knowledge of
Redis/ctx/pub-sub, so it is unit-testable with a mock DB session and
a mock OcrClient. arq tasks are thin wrappers that call into here.
"""
from __future__ import annotations

import logging
import uuid
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from feap.ocr import repository as repo
from feap.ocr import storage
from feap.ocr.client import OcrClient, get_ocr_client
from feap.ocr.errors import OcrError, OcrProviderError
from feap.ocr.schemas import OcrFileExtraction, StructuredProducts
from feap.ocr.structurer import structure_products

logger = logging.getLogger(__name__)


# Hook: structure_products can be swapped in tests
StructureFn = Callable[[list[OcrFileExtraction]], "StructuredProducts"]


async def run_extraction(
    db: AsyncSession,
    *,
    ocr_job_id: uuid.UUID,
    client: OcrClient | None = None,
) -> list[OcrFileExtraction]:
    """Extract text from every file in the job. Persists result to DB.

    Propagates provider errors — caller decides whether to mark job failed
    or continue with partial results.
    """
    client = client or get_ocr_client()
    job = await repo.get_job(db, ocr_job_id)
    if job is None:
        raise OcrError(f"OcrJob {ocr_job_id} not found")

    extractions: list[OcrFileExtraction] = []
    for idx, file_meta in enumerate(job.files or []):
        stored_path = file_meta.get("stored_path")
        if not stored_path:
            raise OcrError(f"File #{idx} has no stored_path")
        content = storage.read_file(stored_path)
        filename = file_meta.get("filename") or f"file_{idx}"
        content_type = file_meta.get("content_type") or "application/octet-stream"

        try:
            provider = await client.extract(
                filename=filename, content=content, content_type=content_type,
            )
        except OcrProviderError as exc:
            logger.warning("OCR provider failed for '%s': %s", filename, exc)
            extractions.append(OcrFileExtraction(
                file_index=idx,
                filename=filename,
                text="",
                warning=f"provider error: {exc}",
            ))
            continue

        text = (provider.data.text if provider.data else "") or ""
        warning = None
        if not text.strip():
            warning = "empty OCR text"
        elif "�" in text:
            # Mojibake placeholder — record but don't block.
            n = text.count("�")
            warning = f"{n} unrecoverable glyphs (non-unicode PDF font)"

        extractions.append(OcrFileExtraction(
            file_index=idx,
            filename=filename,
            text=text,
            processing_time_s=(provider.data.processing_time if provider.data else None),
            provider_task_id=provider.task_id,
            warning=warning,
        ))

    await repo.save_extraction(
        db, ocr_job_id,
        extraction=[e.model_dump() for e in extractions],
    )
    return extractions


async def run_structure(
    db: AsyncSession,
    *,
    ocr_job_id: uuid.UUID,
    extractions: list[OcrFileExtraction],
    structure_fn: StructureFn | None = None,
) -> StructuredProducts:
    """Run the LLM structurer and persist products to the job."""
    fn = structure_fn or structure_products
    result = await fn(extractions)
    await repo.save_completed(
        db, ocr_job_id,
        products=[p.model_dump() for p in result.products],
        skipped_notes=result.skipped_lines or None,
    )
    return result
