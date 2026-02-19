"""Level 0: Product card enrichment schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProductCard(BaseModel):
    """Enriched product description — output of Level 0."""

    original_description: str
    normalized_description: str = Field(
        description="Normalized, cleaned-up description in Russian"
    )
    material: str | None = None
    material_composition_pct: dict[str, float] | None = Field(
        default=None, description="Material composition percentages, e.g. {'хлопок': 80, 'полиэстер': 20}"
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
