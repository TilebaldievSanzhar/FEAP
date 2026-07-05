"""OCR service orchestration — extraction + structure stages with mocks."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from feap.ocr import repository as ocr_repo
from feap.ocr import storage
from feap.ocr.client import OcrClient
from feap.ocr.schemas import (
    OcrFileExtraction,
    OcrProviderData,
    OcrProviderResponse,
    StructuredProduct,
    StructuredProducts,
)
from feap.ocr.service import run_extraction, run_structure


class _StubClient:
    """Returns a canned OcrProviderResponse for every call."""

    def __init__(self, response: OcrProviderResponse):
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def extract(self, *, filename, content, content_type):
        self.calls.append({"filename": filename, "content_type": content_type, "size": len(content)})
        return self._response


class _FailingClient:
    async def extract(self, *, filename, content, content_type):
        from feap.ocr.errors import OcrProviderError
        raise OcrProviderError("upstream exploded")


@pytest.fixture
def job_with_two_files(tmp_path, monkeypatch):
    """Create an OcrJob on disk with two small files — DB-free."""
    from feap.config import settings
    monkeypatch.setattr(settings, "OCR_UPLOAD_DIR", str(tmp_path))

    job_id = uuid.uuid4()
    meta = storage.save_files(job_id, [
        ("a.pdf", b"first content", "application/pdf"),
        ("b.pdf", b"second content", "application/pdf"),
    ])
    return job_id, meta


class _FakeOcrJob:
    def __init__(self, job_id, files):
        self.id = job_id
        self.files = files


class _FakeDB:
    """Minimal AsyncSession stand-in: records all calls we care about."""

    def __init__(self, job):
        self.job = job
        self.saved_extraction: list[dict] | None = None
        self.completed: dict | None = None


async def _fake_get_job(db, job_id):
    return db.job


async def _fake_save_extraction(db, job_id, *, extraction):
    db.saved_extraction = extraction


async def _fake_save_completed(db, job_id, *, products, skipped_notes=None, linked_classification_job_id=None):
    db.completed = {
        "products": products,
        "skipped_notes": skipped_notes,
    }


@pytest.mark.asyncio
async def test_run_extraction_collects_all_files(job_with_two_files, monkeypatch):
    job_id, meta = job_with_two_files
    provider_response = OcrProviderResponse(
        task_id="t1",
        status="completed",
        data=OcrProviderData(text="hello world", processing_time=2.0),
    )
    client = _StubClient(provider_response)
    db = _FakeDB(_FakeOcrJob(job_id, meta))

    monkeypatch.setattr("feap.ocr.service.repo.get_job", _fake_get_job)
    monkeypatch.setattr("feap.ocr.service.repo.save_extraction", _fake_save_extraction)

    extractions = await run_extraction(db, ocr_job_id=job_id, client=client)  # type: ignore[arg-type]

    assert len(extractions) == 2
    assert all(e.text == "hello world" for e in extractions)
    assert [e.file_index for e in extractions] == [0, 1]
    assert db.saved_extraction is not None and len(db.saved_extraction) == 2


@pytest.mark.asyncio
async def test_run_extraction_records_provider_error_per_file(job_with_two_files, monkeypatch):
    job_id, meta = job_with_two_files
    db = _FakeDB(_FakeOcrJob(job_id, meta))

    monkeypatch.setattr("feap.ocr.service.repo.get_job", _fake_get_job)
    monkeypatch.setattr("feap.ocr.service.repo.save_extraction", _fake_save_extraction)

    extractions = await run_extraction(
        db, ocr_job_id=job_id, client=_FailingClient(),  # type: ignore[arg-type]
    )

    assert len(extractions) == 2
    assert all(e.text == "" for e in extractions)
    assert all(e.warning and "provider error" in e.warning for e in extractions)


@pytest.mark.asyncio
async def test_run_extraction_flags_mojibake(job_with_two_files, monkeypatch):
    job_id, meta = job_with_two_files
    provider_response = OcrProviderResponse(
        task_id="t1",
        status="completed",
        data=OcrProviderData(text="Invoice � GT-05927 with ���������� chars"),
    )
    client = _StubClient(provider_response)
    db = _FakeDB(_FakeOcrJob(job_id, meta))

    monkeypatch.setattr("feap.ocr.service.repo.get_job", _fake_get_job)
    monkeypatch.setattr("feap.ocr.service.repo.save_extraction", _fake_save_extraction)

    extractions = await run_extraction(db, ocr_job_id=job_id, client=client)  # type: ignore[arg-type]

    assert all(e.warning and "unrecoverable glyphs" in e.warning for e in extractions)


@pytest.mark.asyncio
async def test_run_structure_uses_override(monkeypatch):
    db = _FakeDB(object())  # save_completed doesn't touch db.job here

    monkeypatch.setattr("feap.ocr.service.repo.save_completed", _fake_save_completed)

    called = {"n": 0}

    async def fake_structurer(extractions):
        called["n"] += 1
        return StructuredProducts(
            products=[StructuredProduct.model_validate({
                "product_card": {
                    "original_description": "RTX 3060",
                    "normalized_description": "Видеокарта RTX 3060",
                },
                "source_ref": {"file_index": 0},
            })],
            skipped_lines=["ignored header"],
        )

    result = await run_structure(
        db,
        ocr_job_id=uuid.uuid4(),
        extractions=[OcrFileExtraction(file_index=0, filename="x.pdf", text="RTX 3060")],
        structure_fn=fake_structurer,
    )

    assert called["n"] == 1
    assert len(result.products) == 1
    assert db.completed is not None
    assert len(db.completed["products"]) == 1
    assert db.completed["skipped_notes"] == ["ignored header"]
