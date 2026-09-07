"""Level 3: Position classification schema."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from feap.schemas.common import BacktrackTrigger
from feap.schemas.codes import Code4, Decision4


class PositionCandidate(BaseModel):
    position: Code4 = Field(description="4-digit position code")
    position_text: str
    # --- SGR: анализ ДО confidence ---
    text_match_score: str = Field(
        default="",
        description="Степень соответствия текста позиции товару: exact/partial/weak",
    )
    reasoning: str
    opi_applied: str = Field(description="Which OPI rule was decisive")
    confidence: float = Field(ge=0.0, le=1.0)


class ShortlistResult(BaseModel):
    """Step 3a: shortlist of 2-5 candidate positions."""

    group: str = ""
    # --- SGR: анализ перед shortlist ---
    physical_nature_analysis: str = Field(
        default="",
        description="Шаг 1: Физическая природа товара — материал, форма, степень обработки",
    )
    key_differentiators: str = Field(
        default="",
        description="Шаг 2: По каким критериям различаются позиции в этой группе",
    )
    # --- Решение ---
    shortlisted: list[Code4] = Field(
        min_length=1, max_length=5, description="List of 4-digit position codes"
    )
    reasoning: str


class PositionResult(BaseModel):
    """Output of Level 3 — position classification (step 3b deep analysis)."""

    section: str = ""
    group: str = ""
    # --- SGR: пошаговый анализ ОПИ ---
    step1_product_nature: str = Field(
        default="",
        description="Шаг 1: Физическая природа товара (материал, форма, обработка)",
    )
    step2_opi1_text_analysis: str = Field(
        default="",
        description="Шаг 2: ОПИ 1 — какие тексты позиций наиболее точно описывают товар",
    )
    step3_ambiguity_resolution: str = Field(
        default="",
        description="Шаг 3: Если неоднозначно — применение ОПИ 2/3. Если однозначно — 'Однозначно по ОПИ 1'",
    )
    # --- Решение (ПОСЛЕ анализа) ---
    candidates: list[PositionCandidate] = Field(min_length=1, max_length=5)
    selected_position: Decision4
    applied_rules: list[str] = Field(default_factory=list)
    precedents_found: list[str] = Field(default_factory=list)
    missing_parameters: list[str] = Field(
        default_factory=list,
        description="Parameters needed but not provided — requires user input",
    )
    backtrack_trigger: BacktrackTrigger | None = None

    @field_validator("missing_parameters", mode="before")
    @classmethod
    def _coerce_to_list(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [v] if v.strip() else []
        if v is None:
            return []
        return v
