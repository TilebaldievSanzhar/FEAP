"""Pydantic schemas for the OCR module — API contracts + LLM output shape."""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from feap.schemas.product_card import ProductCard


# ── Provider response (ocr.trade.kg /ocr/process) ─────────────────────────────

class OcrProviderMetrics(BaseModel):
    """Opaque per-call metrics returned by the provider — kept for debugging."""

    model_config = {"extra": "allow"}

    inference_time: float | None = None
    total_time: float | None = None
    tensorrt_enabled: bool | None = None
    cache_hit: bool | None = None
    file_type: str | None = None


class OcrProviderData(BaseModel):
    filename: str | None = None
    text: str = ""
    task_id: str | None = None
    metadata: dict | None = None
    processing_time: float | None = None
    metrics: OcrProviderMetrics | None = None


class OcrProviderResponse(BaseModel):
    """Envelope returned by the upstream OCR service."""

    task_id: str | None = None
    status: str = ""
    success: bool | None = None
    data: OcrProviderData | None = None
    error: str | None = None


# ── Per-file extraction result stored in OcrJob.extraction ────────────────────

class OcrFileExtraction(BaseModel):
    """One element of OcrJob.extraction — what we got back per uploaded file."""

    file_index: int
    filename: str
    text: str = ""
    processing_time_s: float | None = None
    provider_task_id: str | None = None
    warning: str | None = Field(
        default=None,
        description="Non-fatal issues: empty text, mojibake detected, etc.",
    )


# ── LLM structure output (level 98) ───────────────────────────────────────────

class StructuredProduct(BaseModel):
    """One product extracted by the LLM from raw OCR text."""

    product_card: ProductCard
    # Kept as a plain dict downstream (stored as JSONB, spread into
    # BatchItemInput.source_ref), but the JSON Schema advertises its keys
    # explicitly: OpenAI's strict mode rejects free-form objects.
    source_ref: dict = Field(
        default_factory=dict,
        description="Hints back to the source: {file_index, line_hint, hs_code_hint, quantity, unit, unit_price, currency, country_origin}",
        json_schema_extra={
            "properties": {
                "file_index": {"type": ["integer", "null"]},
                "line_hint": {"type": ["string", "null"]},
                "hs_code_hint": {"type": ["string", "null"]},
                "quantity": {"type": ["number", "null"]},
                "unit": {"type": ["string", "null"]},
                "unit_price": {"type": ["number", "null"]},
                "currency": {"type": ["string", "null"]},
                "country_origin": {"type": ["string", "null"]},
            },
        },
    )


class StructuredProducts(BaseModel):
    """Output of the OCR structure LLM step (level 98).

    The LLM reads concatenated OCR text from N files and returns:
    - products: a list of atomic products suitable for the cascade
    - skipped_lines: free-text notes about content that was discarded (services,
      bank details, shipping/packing rows, etc.)
    - document_types_detected: best guess per file (`invoice`, `specification`,
      `contract`, `other`) — informational only.
    """

    products: list[StructuredProduct] = Field(default_factory=list)
    skipped_lines: list[str] = Field(default_factory=list)
    document_types_detected: list[str] = Field(default_factory=list)
    summary: str | None = None


# ── API request/response ──────────────────────────────────────────────────────

OcrJobStatus = Literal[
    "pending", "extracting", "structuring", "completed", "failed", "cancelled"
]


class OcrJobFileMeta(BaseModel):
    filename: str
    size_bytes: int
    content_type: str
    sha256: str


class OcrJobSubmitResponse(BaseModel):
    ocr_job_id: uuid.UUID
    status: OcrJobStatus
    files_count: int
    auto_classify: bool
    poll_url: str


class OcrJobResponse(BaseModel):
    ocr_job_id: uuid.UUID
    status: OcrJobStatus
    stage: str | None = None
    auto_classify: bool
    files: list[OcrJobFileMeta] = Field(default_factory=list)
    extraction: list[OcrFileExtraction] | None = None
    products: list[StructuredProduct] | None = None
    skipped_notes: list[str] | None = None
    linked_classification_job_id: uuid.UUID | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    expires_at: str
