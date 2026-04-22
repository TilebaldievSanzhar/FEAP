"""Level 5: Subsubposition (8/10-digit) classification schema.

SGR fields guide reasoning before the decision:
  L5a — relevant_parameters + code_differentiation → select 8-digit branch
  L5b — relevant_parameters + code_differentiation + threshold_comparison → select 10-digit code
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class BranchCandidate(BaseModel):
    """Candidate branch (8-digit code group) for L5a shortlist."""

    code_8: str = Field(description="8-значный код ветки")
    branch_text: str = Field(default="", description="Текст/название ветки")
    reasoning: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SubsubpositionShortlistResult(BaseModel):
    """Output of Level 5a — 8-digit branch selection."""

    subposition: str = Field(default="", description="6-digit parent subposition")

    # --- SGR: guided reasoning ---
    relevant_parameters: str = Field(
        default="",
        description="Какие параметры товара влияют на выбор ветки? "
        "Перечисли из карточки. Если параметр не указан — «НЕ УКАЗАНО».",
    )
    code_differentiation: str = Field(
        default="",
        description="Прочитай ПОЛНЫЕ описания веток. Чем различаются? "
        "Для каждой: «ветка A = ПОЛНОЕ ОПИСАНИЕ, ветка B = ПОЛНОЕ ОПИСАНИЕ, ключевое различие = ...».",
    )

    candidates: list[BranchCandidate] = Field(min_length=1, max_length=5)
    selected_code_8: str = Field(description="Выбранный 8-значный код")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SubsubpositionCandidate(BaseModel):
    code_10: str = Field(description="10-digit code")
    code_text: str = Field(default="", description="Name/text of the code")
    reasoning: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SubsubpositionResult(BaseModel):
    """Output of Level 5 — final 10-digit code."""

    subposition: str = Field(default="", description="6-digit parent subposition")

    # --- SGR: guided reasoning ---
    relevant_parameters: str = Field(
        default="",
        description="Какие параметры товара влияют на выбор кода? "
        "Перечисли из карточки. Если параметр не указан — «НЕ УКАЗАНО».",
    )
    code_differentiation: str = Field(
        default="",
        description="Прочитай ПОЛНЫЕ описания кодов с учётом [ВЕТКИ]. "
        "Чем различаются? «код A (ветка X) = ..., код B (ветка Y) = ..., различие = ...».",
    )
    threshold_comparison: str = Field(
        default="",
        description="Если в текстах кодов есть числовые пороги — сопоставь: "
        "«параметр = X, порог = Y, X > Y → код Z». "
        "Если порогов нет — «порогов нет, выбор по тексту».",
    )

    # --- Candidates + decision ---
    candidates: list[SubsubpositionCandidate] = Field(
        default_factory=list,
        description="Кандидаты с обоснованием",
    )
    selected_code_8: str = Field(description="8-digit code")
    selected_code_10: str = Field(description="10-digit code")
    reasoning: str = Field(default="", description="Step-by-step reasoning for the selected code")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_parameters: list[str] = Field(
        default_factory=list,
        description="Parameters needed but not provided — requires user input",
    )
    final_code: str = Field(default="", description="Final formatted code (computed post-hoc)")

    @field_validator("missing_parameters", mode="before")
    @classmethod
    def _coerce_to_list(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [v] if v.strip() else []
        if v is None:
            return []
        return v
