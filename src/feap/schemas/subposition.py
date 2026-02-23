"""Level 4: Subposition classification schema."""

from __future__ import annotations

from pydantic import BaseModel, Field

from feap.schemas.common import BacktrackTrigger


class SubpositionCandidate(BaseModel):
    subposition: str = Field(description="6-digit subposition code")
    subposition_text: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)


class SubpositionResult(BaseModel):
    """Output of Level 4 — subposition classification."""

    position: str
    candidates: list[SubpositionCandidate] = Field(min_length=1, max_length=5)
    selected_subposition: str
    applied_rules: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    missing_parameters: list[str] = Field(
        default_factory=list,
        description="Parameters needed but not provided — requires user input",
    )
    backtrack_trigger: BacktrackTrigger | None = None
