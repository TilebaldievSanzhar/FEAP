"""Level 3: Position classification schema."""

from __future__ import annotations

from pydantic import BaseModel, Field

from feap.schemas.common import BacktrackTrigger


class PositionCandidate(BaseModel):
    position: str = Field(description="4-digit position code")
    position_text: str
    reasoning: str
    opi_applied: str = Field(description="Which OPI rule was decisive")
    confidence: float = Field(ge=0.0, le=1.0)


class ShortlistResult(BaseModel):
    """Step 3a: shortlist of 2-5 candidate positions."""

    group: str
    shortlisted: list[str] = Field(
        min_length=1, max_length=5, description="List of 4-digit position codes"
    )
    reasoning: str


class PositionResult(BaseModel):
    """Output of Level 3 — position classification (step 3b deep analysis)."""

    section: str
    group: str
    candidates: list[PositionCandidate] = Field(min_length=1, max_length=5)
    selected_position: str
    applied_rules: list[str] = Field(default_factory=list)
    precedents_found: list[str] = Field(default_factory=list)
    missing_parameters: list[str] = Field(
        default_factory=list,
        description="Parameters needed but not provided — requires user input",
    )
    backtrack_trigger: BacktrackTrigger | None = None
