"""Level 1: Section classification schema."""

from __future__ import annotations

from pydantic import BaseModel, Field

from feap.schemas.common import BacktrackTrigger


class SectionShortlistResult(BaseModel):
    """Step 1a: shortlist of 2-3 candidate sections (without notes)."""

    # --- SGR: анализ перед выбором ---
    product_nature: str = Field(
        default="",
        description="Шаг 1: Физическая природа товара (материал, форма, назначение)",
    )
    primary_section_signal: str = Field(
        default="",
        description="Шаг 2: Главный признак, указывающий на раздел",
    )
    # --- Решение ---
    shortlisted: list[str] = Field(
        min_length=1, max_length=3,
        description="List of Roman numerals (I-XXI)",
    )
    reasoning: str


class SectionCandidate(BaseModel):
    section: str = Field(description="Roman numeral (I-XXI)")
    section_name: str
    # --- SGR: анализ ДО confidence ---
    text_match_analysis: str = Field(
        default="",
        description="Как текст раздела соотносится с товаром",
    )
    exclusion_check: str = Field(
        description="Check of exclusion notes for this section"
    )
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)


class SectionResult(BaseModel):
    """Step 1b: final section selection with notes verification."""

    # --- SGR: сводка по примечаниям ---
    key_notes_analysis: str = Field(
        default="",
        description="Ключевые примечания, повлиявшие на решение",
    )
    # --- Решение ---
    candidates: list[SectionCandidate] = Field(min_length=1, max_length=3)
    selected_section: str = Field(description="Roman numeral of selected section")
    applied_rules: list[str] = Field(default_factory=list)
    backtrack_trigger: BacktrackTrigger | None = None
