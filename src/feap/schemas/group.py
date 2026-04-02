"""Level 2: Group classification schema."""

from __future__ import annotations

from pydantic import BaseModel, Field

from feap.schemas.common import BacktrackTrigger


class GroupCandidate(BaseModel):
    group: str = Field(description="2-digit group code")
    group_name: str
    # --- SGR: анализ ДО confidence ---
    text_match_analysis: str = Field(
        default="",
        description="Соответствие текста группы товару",
    )
    exclusion_check: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)


class GroupResult(BaseModel):
    """Output of Level 2 — group classification."""

    section: str = ""
    # --- SGR: контекстный анализ ---
    product_in_section_context: str = Field(
        default="",
        description="Шаг 1: Как товар вписывается в контекст раздела",
    )
    key_exclusion_notes: str = Field(
        default="",
        description="Шаг 2: Какие исключающие примечания проверены",
    )
    # --- Решение ---
    candidates: list[GroupCandidate] = Field(min_length=1, max_length=5)
    selected_group: str
    applied_rules: list[str] = Field(default_factory=list)
    backtrack_trigger: BacktrackTrigger | None = None
