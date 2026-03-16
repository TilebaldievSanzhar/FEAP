"""Validation schema for the final classification result."""

from __future__ import annotations

from pydantic import BaseModel, Field

from feap.schemas.common import DecisionChainEntry


class ClassificationChain(BaseModel):
    section: DecisionChainEntry
    group: DecisionChainEntry
    position: DecisionChainEntry
    subposition: DecisionChainEntry
    code_10: DecisionChainEntry


class ClassificationResult(BaseModel):
    """Final validated classification result."""

    final_code: str = Field(description="10-digit ТН ВЭД code")
    code_formatted: str = Field(description="Formatted code like '8509 80 000 0'")
    code_description: str
    duty_rate: str | None = None
    vat_rate: str | None = None
    classification_chain: ClassificationChain
    opi_applied: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    risk_flags: list[str] = Field(default_factory=list)
    precedent_match: bool = False
    precedent_codes: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


class PartialResult(BaseModel):
    """Returned when classification can't complete — needs user input."""

    partial_code: str
    determined_to_level: int
    missing_parameters: list[str]
    question: str
    questions: list[str] = Field(
        default_factory=list,
        description="Specific clarifying questions for the user",
    )
    classification_chain_so_far: list[DecisionChainEntry] = Field(default_factory=list)
