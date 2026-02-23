"""API request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from feap.schemas.validation import ClassificationResult, PartialResult


class ClassifyRequest(BaseModel):
    description: str = Field(min_length=3, description="Product description")
    additional_info: dict[str, str] = Field(
        default_factory=dict, description="Additional product parameters"
    )


class ClassifyResponse(BaseModel):
    session_id: str
    status: str = Field(description="completed | needs_input | error")
    result: ClassificationResult | None = None
    partial: PartialResult | None = None
    error: str | None = None


class AnswerRequest(BaseModel):
    answers: dict[str, str] = Field(
        description="Key-value answers to clarifying questions, e.g. {'мощность': '1.5 кВт'}"
    )


class TnvedNodeResponse(BaseModel):
    code: str
    code_formatted: str | None
    level: int
    name: str
    duty_rate: str | None
    children_count: int = 0
