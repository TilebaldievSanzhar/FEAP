"""Local filesystem storage for uploaded OCR files.

MVP: files land in OCR_UPLOAD_DIR/<ocr_job_id>/<sha256>_<filename>.
When a worker picks the job up, it reads files back from disk and
passes them to OcrClient. After the job completes (or fails) the
directory can be cleaned up by the TTL cron — not implemented here
yet since files can still be useful for debugging.

Swap to S3/MinIO later by implementing the same module-level API
(save_files, read_file, delete_job_dir) against boto3 / minio client.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from pathlib import Path

from feap.config import settings
from feap.ocr.errors import OcrFileRejected

logger = logging.getLogger(__name__)


def _job_dir(ocr_job_id: uuid.UUID) -> Path:
    return Path(settings.OCR_UPLOAD_DIR) / str(ocr_job_id)


def save_files(
    ocr_job_id: uuid.UUID,
    files: list[tuple[str, bytes, str]],
) -> list[dict]:
    """Persist uploaded files on disk. Returns list of file metadata dicts.

    Each input tuple: (filename, content, content_type). Returns metadata
    in the shape stored in `ocr_jobs.files`:
        {filename, size_bytes, sha256, content_type, stored_path}
    """
    if len(files) > settings.OCR_MAX_FILES_PER_JOB:
        raise OcrFileRejected(
            f"Too many files: {len(files)} > limit {settings.OCR_MAX_FILES_PER_JOB}"
        )

    max_bytes = settings.OCR_MAX_FILE_MB * 1024 * 1024
    for filename, content, _ct in files:
        if len(content) > max_bytes:
            raise OcrFileRejected(
                f"File '{filename}' is {len(content)} bytes, exceeds "
                f"limit {settings.OCR_MAX_FILE_MB} MB"
            )
        if len(content) == 0:
            raise OcrFileRejected(f"File '{filename}' is empty")

    job_dir = _job_dir(ocr_job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    meta: list[dict] = []
    for filename, content, content_type in files:
        sha = hashlib.sha256(content).hexdigest()
        safe_name = Path(filename).name.replace("\0", "_")
        stored_path = job_dir / f"{sha[:16]}_{safe_name}"
        stored_path.write_bytes(content)
        meta.append({
            "filename": safe_name,
            "size_bytes": len(content),
            "sha256": sha,
            "content_type": content_type,
            "stored_path": str(stored_path),
        })
    return meta


def read_file(stored_path: str) -> bytes:
    return Path(stored_path).read_bytes()


def delete_job_dir(ocr_job_id: uuid.UUID) -> None:
    """Best-effort cleanup. Safe to call even if the dir never existed."""
    job_dir = _job_dir(ocr_job_id)
    if not job_dir.exists():
        return
    try:
        shutil.rmtree(job_dir)
    except OSError as exc:  # noqa: BLE001
        logger.warning("Failed to delete %s: %s", job_dir, exc)
