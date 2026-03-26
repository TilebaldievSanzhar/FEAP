"""Level 4: Subposition classification schema."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from feap.schemas.common import BacktrackTrigger


class SubpositionCandidate(BaseModel):
    subposition: str = Field(description="6-digit subposition code")
    subposition_text: str
    category_match: str = Field(
        default="",
        description="Тип соответствия: 'direct' (товар прямо назван в тексте субпозиции), "
        "'by_parameter' (товар попадает по числовому порогу/диапазону), "
        "'residual' (товар не подходит ни под одну конкретную — только «прочие»)",
    )
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)


class SubpositionResult(BaseModel):
    """Output of Level 4 — subposition classification."""

    position: str
    # --- SGR Step 1: Тип товара (дискретный выбор) ---
    product_type: str = Field(
        default="",
        description="Выбери ОДНО: 'device' (целое устройство/изделие), 'part' (часть), "
        "'accessory' (принадлежность), 'material' (сырьё/полуфабрикат), 'set' (набор/комплект).",
    )
    # --- SGR Step 2: Код лучшего кандидата ДО анализа (первое впечатление) ---
    best_candidate_code: str = Field(
        default="",
        description="Какой код субпозиции ЛУЧШЕ ВСЕГО подходит по ТЕКСТУ? "
        "Напиши 6-значный код. Это предварительный выбор — можешь изменить после анализа.",
    )
    # --- SGR Step 3: Почему именно этот, а не «прочие»? ---
    why_not_residual: str = Field(
        default="",
        description="Если best_candidate_code НЕ является 'прочие' — объясни, КАКОЙ ТЕКСТ "
        "субпозиции ПРЯМО описывает товар. Если best_candidate_code = 'прочие' — "
        "объясни, почему НИ ОДНА конкретная субпозиция не подходит.",
    )
    # --- SGR Step 4: Проверка уровня дефисов ---
    dash_level_check: str = Field(
        default="",
        description="Проверь: best_candidate_code — ОДНОДЕФИСНАЯ или ДВУХДЕФИСНАЯ субпозиция? "
        "Если есть однодефисная с ТАКИМ ЖЕ текстом — выбери ЕЁ (ОПИ 6). "
        "Формат: 'код X = однодефисная/двухдефисная, выбор верный/нет'.",
    )
    # --- Решение (подтверждение или изменение) ---
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
