"""Level 5: Subsubposition (8/10-digit) classification schema."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class BranchCandidate(BaseModel):
    """Candidate branch (8-digit code group) for L5a shortlist."""

    code_8: str = Field(description="8-значный код ветки")
    branch_text: str = Field(default="", description="Текст/название ветки")
    match_type: str = Field(
        default="",
        description="'direct' / 'by_parameter' / 'residual'",
    )
    reasoning: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SubsubpositionShortlistResult(BaseModel):
    """Output of Level 5a — 8-digit branch selection."""

    subposition: str = Field(default="", description="6-digit parent subposition")
    relevant_parameters: str = Field(
        default="",
        description="ПРОЦИТИРУЙ из карточки товара параметры, влияющие на выбор ветки. "
        "Если параметр отсутствует — 'НЕ УКАЗАНО'.",
    )
    branch_differentiation: str = Field(
        default="",
        description="Чем КОНКРЕТНО различаются ветки? Для каждой: "
        "'ветка A = ...', 'ветка B = ...', различие = '...'.",
    )
    candidates: list[BranchCandidate] = Field(min_length=1, max_length=5)
    selected_code_8: str = Field(description="Выбранный 8-значный код")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SubsubpositionCandidate(BaseModel):
    code_10: str = Field(description="10-digit code")
    code_text: str = Field(default="", description="Name/text of the code")
    match_type: str = Field(
        default="",
        description="'direct' (товар прямо назван), "
        "'by_parameter' (попадает по числовому порогу), "
        "'residual' (остаточная категория 'прочие')",
    )
    reasoning: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SubsubpositionResult(BaseModel):
    """Output of Level 5 — final 10-digit code."""

    subposition: str = Field(default="", description="6-digit parent subposition")
    # --- SGR: анализ перед выбором кода ---
    parent_context: str = Field(
        default="",
        description="Шаг 0: Что означает текущая субпозиция? "
        "Какого типа товары здесь классифицируются? Товар соответствует этой категории?",
    )
    relevant_parameters: str = Field(
        default="",
        description="Шаг 1: ПРОЦИТИРУЙ из карточки товара параметры, влияющие на выбор кода. "
        "Если параметр отсутствует — 'НЕ УКАЗАНО'.",
    )
    code_differentiation: str = Field(
        default="",
        description="Шаг 2: Чем КОНКРЕТНО различаются коды? Перечисли: "
        "код A = '...', код B = '...', различие = '...'.",
    )
    threshold_comparison: str = Field(
        default="",
        description="Шаг 3: Сопоставление: 'параметр = X, порог кода = Y, X > Y → код Z'.",
    )
    # --- Кандидаты (ПЕРЕД решением — модель анализирует варианты) ---
    candidates: list[SubsubpositionCandidate] = Field(
        default_factory=list,
        description="Кандидаты с reasoning и match_type для каждого",
    )
    # --- Решение (ПОСЛЕ анализа кандидатов) ---
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
