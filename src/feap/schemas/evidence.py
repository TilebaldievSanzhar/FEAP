"""Grounded discriminators, generated before the classification decision."""
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, StrictInt


class DiscriminatorEvidence(BaseModel):
    code: str = Field(description='Код, условие которого проверяется; только из списка')
    alternative_code: str = Field(description='Ближайший другой допустимый код')
    parameter: str = Field(min_length=1, description='Один признак, различающий эти варианты')
    source_ids: list[StrictInt] = Field(
        max_length=6, description='Номера фрагментов SOURCE из исходного описания, подтверждающих проверку; [] для unknown')
    comparison: str = Field(description='Краткое сопоставление признака и условия, без предположений')
    status: Literal['supported', 'contradicted', 'unknown']
    _criterion_quote: str = PrivateAttr(default='')
    _source_quote: str = PrivateAttr(default='')
    _reported_status: str | None = PrivateAttr(default=None)
    _grounding_issue: str | None = PrivateAttr(default=None)


class EvidenceFields(BaseModel):
    evidence_checks: list[DiscriminatorEvidence] = Field(
        min_length=1, max_length=8,
        description='Проверки различающих условий выбранного кода и ближайшей альтернативы. '
        'Неизвестный признак не означает опровержение конкретной ветки.',
    )
