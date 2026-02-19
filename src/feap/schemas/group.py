"""Level 2: Group classification schema."""

from __future__ import annotations

from pydantic import BaseModel, Field

from feap.schemas.common import BacktrackTrigger


class GroupCandidate(BaseModel):
    group: str = Field(description="2-digit group code")
    group_name: str
    reasoning: str
    exclusion_check: str
    confidence: float = Field(ge=0.0, le=1.0)


class GroupResult(BaseModel):
    """Output of Level 2 — group classification."""

    section: str
    candidates: list[GroupCandidate] = Field(min_length=1, max_length=5)
    selected_group: str
    applied_rules: list[str] = Field(default_factory=list)
    backtrack_trigger: BacktrackTrigger | None = None
