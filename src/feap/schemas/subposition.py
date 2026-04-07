"""Level 4: Subposition classification schema.

SGR fields guide the model's reasoning before the decision:
  1. Extract relevant parameters from the product card
  2. Compare with numeric thresholds in subposition texts
  3. Determine if a specific code names the product or it falls into 'other'
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from feap.schemas.common import BacktrackTrigger


class SubpositionCandidate(BaseModel):
    subposition: str = Field(description="6-digit subposition code")
    subposition_text: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)


class SubpositionResult(BaseModel):
    """Output of Level 4 — subposition classification."""

    position: str = ""

    # --- SGR: guided reasoning (3 fields, then decide) ---
    parameter_extraction: str = Field(
        default="",
        description="Какие характеристики товара влияют на выбор субпозиции? "
        "Перечисли из карточки: материал, масса, мощность, объём, назначение и т.д. "
        "Формат: «масса = 3.1 т», «материал = полиэстер». "
        "Если параметр не указан — «НЕ УКАЗАНО».",
    )
    threshold_analysis: str = Field(
        default="",
        description="Есть ли в текстах субпозиций числовые пороги (масса, мощность, объём и т.д.)? "
        "Если да — сопоставь: «параметр = X, порог = «не более Y», X ≤ Y → да». "
        "Если порогов нет — «порогов нет, выбор по тексту».",
    )
    specificity_check: str = Field(
        default="",
        description="Есть ли среди субпозиций код, который КОНКРЕТНО называет/описывает этот товар? "
        "Или товар не подходит ни под один конкретный код и попадает в «прочие»? "
        "Обоснуй.",
    )

    # --- Candidates + decision ---
    candidates: list[SubpositionCandidate] = Field(min_length=1, max_length=5)
    selected_subposition: str
    applied_rules: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
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
