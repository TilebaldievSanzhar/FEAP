"""Level 1: Section classification schema."""

from __future__ import annotations

from pydantic import BaseModel, Field

from feap.schemas.common import BacktrackTrigger


class SectionShortlistResult(BaseModel):
    """Step 1a: shortlist of 2-3 candidate sections (without notes)."""

    shortlisted: list[str] = Field(
        min_length=1, max_length=3,
        description="List of Roman numerals (I-XXI)",
    )
    reasoning: str


class SectionCandidate(BaseModel):
    section: str = Field(description="Roman numeral (I-XXI)")
    section_name: str
    reasoning: str
    exclusion_check: str = Field(
        description="Check of exclusion notes for this section"
    )
    confidence: float = Field(ge=0.0, le=1.0)


class SectionResult(BaseModel):
    """Step 1b: final section selection with notes verification."""

    candidates: list[SectionCandidate] = Field(min_length=1, max_length=3)
    selected_section: str = Field(description="Roman numeral of selected section")
    applied_rules: list[str] = Field(default_factory=list)
    backtrack_trigger: BacktrackTrigger | None = None
