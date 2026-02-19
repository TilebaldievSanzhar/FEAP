"""Level 5: Subsubposition (8/10-digit) classification schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SubsubpositionResult(BaseModel):
    """Output of Level 5 — final 10-digit code."""

    subposition: str = Field(description="6-digit parent subposition")
    selected_code_8: str = Field(description="8-digit code")
    selected_code_10: str = Field(description="10-digit code")
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    missing_parameters: list[str] = Field(
        default_factory=list,
        description="Parameters needed but not provided — requires user input",
    )
    final_code: str = Field(description="Final formatted code")
