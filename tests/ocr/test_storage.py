"""Local filesystem storage guards — size/count/empty limits."""
from __future__ import annotations

import uuid

import pytest

from feap.config import settings
from feap.ocr import storage
from feap.ocr.errors import OcrFileRejected


@pytest.fixture
def tmp_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OCR_UPLOAD_DIR", str(tmp_path))
    return tmp_path


def test_save_files_writes_to_disk(tmp_upload_dir):
    job_id = uuid.uuid4()
    meta = storage.save_files(
        job_id, [("invoice.pdf", b"%PDF-1.7 small content", "application/pdf")]
    )
    assert len(meta) == 1
    assert meta[0]["filename"] == "invoice.pdf"
    assert meta[0]["content_type"] == "application/pdf"
    assert meta[0]["size_bytes"] == len(b"%PDF-1.7 small content")
    assert meta[0]["sha256"]  # non-empty
    assert storage.read_file(meta[0]["stored_path"]) == b"%PDF-1.7 small content"


def test_save_files_rejects_empty(tmp_upload_dir):
    with pytest.raises(OcrFileRejected):
        storage.save_files(uuid.uuid4(), [("x.pdf", b"", "application/pdf")])


def test_save_files_rejects_oversize(tmp_upload_dir, monkeypatch):
    monkeypatch.setattr(settings, "OCR_MAX_FILE_MB", 1)
    too_big = b"A" * (2 * 1024 * 1024)  # 2 MB
    with pytest.raises(OcrFileRejected):
        storage.save_files(uuid.uuid4(), [("big.pdf", too_big, "application/pdf")])


def test_save_files_rejects_too_many(tmp_upload_dir, monkeypatch):
    monkeypatch.setattr(settings, "OCR_MAX_FILES_PER_JOB", 2)
    with pytest.raises(OcrFileRejected):
        storage.save_files(
            uuid.uuid4(),
            [(f"f{i}.pdf", b"x", "application/pdf") for i in range(3)],
        )


def test_delete_job_dir_is_idempotent(tmp_upload_dir):
    job_id = uuid.uuid4()
    storage.save_files(job_id, [("f.pdf", b"content", "application/pdf")])
    storage.delete_job_dir(job_id)
    # second call is a no-op
    storage.delete_job_dir(job_id)
