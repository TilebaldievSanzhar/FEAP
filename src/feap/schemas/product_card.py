"""Level 0: Product card enrichment schema."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class MaterialComponent(BaseModel):
    """One material component with its percentage."""

    name: str = Field(description="Material name, e.g. 'хлопок'")
    percentage: float | None = Field(default=None, description="Percentage 0-100, or null if unknown")


class ProductCard(BaseModel):
    """Enriched product description — output of Level 0."""

    original_description: str
    # --- SGR: анализ перед заполнением карточки ---
    product_identification: str = Field(
        default="",
        description="Шаг 1: Что это за товар? Тип, категория, базовое назначение",
    )
    classification_relevant_features: str = Field(
        default="",
        description="Шаг 2: Какие характеристики важны для таможенной классификации",
    )
    # --- Основные поля карточки ---
    normalized_description: str = Field(
        description="Normalized, cleaned-up description in Russian"
    )
    material: str | None = None
    material_composition_pct: list[MaterialComponent] | None = Field(
        default=None, description="Material composition percentages, e.g. [{'name': 'хлопок', 'percentage': 80}]"
    )
    function: str | None = Field(default=None, description="Primary function of the product")
    processing_degree: str | None = Field(
        default=None, description="сырьё | полуфабрикат | готовое изделие"
    )
    physical_state: str | None = Field(
        default=None, description="твёрдое | жидкое | газообразное | порошок | etc."
    )
    dimensions: str | None = None
    weight: str | None = None
    packaging: str | None = None
    is_set: bool = False
    is_disassembled: bool = False
    has_electric_motor: bool = False
    power_source: str | None = Field(
        default=None, description="электричество | бензин | ручной | etc."
    )
    intended_use: str | None = Field(
        default=None, description="бытовой | промышленный | медицинский | etc."
    )
    missing_info: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)

    @field_validator("missing_info", "clarifying_questions", mode="before")
    @classmethod
    def _coerce_to_list(cls, v: object) -> list[str]:
        """LLM sometimes returns a string instead of a list — coerce it."""
        if isinstance(v, str):
            return [v] if v.strip() else []
        if v is None:
            return []
        return v
