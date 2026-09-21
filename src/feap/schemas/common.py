"""Common schema types shared across cascade levels."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BacktrackTrigger(BaseModel):
    """Signal to return to a previous cascade level."""

    target_level: int = Field(description="Level to return to (0-based)")
    excluded_code: str = Field(description="Code to exclude on retry")
    reason: str = Field(description="Why backtracking is needed")


class Candidate(BaseModel):
    """A candidate at any cascade level."""

    code: str
    name: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)


class DecisionChainEntry(BaseModel):
    """One step in the classification decision chain."""

    level: int
    code: str
    name: str
    reasoning: str
    rules_applied: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_checks: list[dict] = Field(default_factory=list)
