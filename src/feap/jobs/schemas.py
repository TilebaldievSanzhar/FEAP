"""API schemas for async classification jobs."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from feap.schemas.validation import ClassificationResult, PartialResult


# ── Request schemas ──────────────────────────────────────────────────────────

class BatchItemInput(BaseModel):
    """One product description in a batch request."""

    description: str = Field(min_length=1)
    additional_info: dict[str, str] = Field(default_factory=dict)
    source_ref: dict | None = Field(
        default=None,
        description="Free-form source reference, e.g. {'ocr_line_number': 7}",
    )


BatchMode = Literal["auto", "sync", "async"]


class ClassifyBatchRequest(BaseModel):
    items: list[BatchItemInput] = Field(min_length=1)
    mode: BatchMode = "auto"


class JobAnswerRequest(BaseModel):
    """Answers for a single needs_input item."""

    answers: dict[str, str]


class JobAnswerEntry(BaseModel):
    item_id: uuid.UUID
    answers: dict[str, str]


class JobAnswersBatchRequest(BaseModel):
    """Answers for multiple needs_input items at once."""

    answers: list[JobAnswerEntry] = Field(min_length=1)


# ── Response schemas ─────────────────────────────────────────────────────────

ItemStatus = Literal[
    "pending", "running", "completed", "needs_input", "failed", "cancelled"
]
JobStatus = Literal[
    "pending", "running", "completed", "needs_input", "partial", "failed", "cancelled"
]


class BatchSyncItemResult(BaseModel):
    position: int
    status: ItemStatus
    session_id: str | None = None
    result: ClassificationResult | None = None
    partial: PartialResult | None = None
    error: str | None = None


class BatchSyncResponse(BaseModel):
    results: list[BatchSyncItemResult]


class BatchAsyncResponse(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    total_items: int
    poll_url: str
    items_url: str
    events_url: str


class JobProgress(BaseModel):
    total: int
    pending: int
    running: int
    completed: int
    failed: int
    needs_input: int
    cancelled: int


class JobResponse(BaseModel):
    job_id: uuid.UUID
    job_type: str
    status: JobStatus
    progress: JobProgress
    parent_ocr_job_id: uuid.UUID | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    expires_at: str


class JobItemResponse(BaseModel):
    item_id: uuid.UUID
    position: int
    status: ItemStatus
    product_description: str
    source_ref: dict | None = None
    session_id: str | None = None
    result: ClassificationResult | None = None
    partial: PartialResult | None = None
    error: str | None = None


class JobItemsResponse(BaseModel):
    items: list[JobItemResponse]
    next_cursor: str | None = None


class JobItemAnswerResponse(BaseModel):
    """Result of answering a single item's clarifying questions."""

    item_id: uuid.UUID
    status: ItemStatus
    result: ClassificationResult | None = None
    partial: PartialResult | None = None
    error: str | None = None


class JobAnswersBatchResponse(BaseModel):
    answered: list[JobItemAnswerResponse]
    job_status: JobStatus
