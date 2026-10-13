"""Таможенные сборы КР за таможенные операции (ПП КР № 79 от 13.02.2020, п. 38).

Действующая редакция п. 38 (ПКМ № 349 от 03.07.2024, № 406 от 10.07.2025,
№ 444 от 29.06.2026): при помещении под таможенные процедуры — 0,4% от
таможенной стоимости, не менее 5 и не более 2500 расчётных показателей (РП).
Транзит — 0,4% от «представленной стоимости» (вне скоупа ИМ 40);
предварительное решение по классификации/происхождению — 10 РП.
Сбор уплачивается одновременно с подачей декларации (п. 36), то есть
считается один раз на ДТ от общей таможенной стоимости (графа 12).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")

KG_RELEASE_FEE_LEGAL_REF = (
    "ПП КР № 79 от 13.02.2020, п. 38 (ред. ПКМ № 349 от 03.07.2024, "
    "№ 406 от 10.07.2025, № 444 от 29.06.2026)"
)


@dataclass(frozen=True)
class FeeRule:
    """Процентный сбор с вилкой в расчётных показателях."""

    pct: Decimal
    min_rp: Decimal
    max_rp: Decimal
    rp_kgs: Decimal
    legal_ref: str

    @property
    def min_kgs(self) -> Decimal:
        return self.min_rp * self.rp_kgs

    @property
    def max_kgs(self) -> Decimal:
        return self.max_rp * self.rp_kgs


@dataclass(frozen=True)
class FeeResult:
    amount: Decimal
    basis: Decimal
    rate_text: str
    bound: str | None  # 'min' | 'max' | None — сработало ли ограничение


def kg_release_fee_rule(rp_kgs: Decimal = Decimal(100)) -> FeeRule:
    """Сбор за выпуск по п. 38. rp_kgs — размер РП в сомах (настройка)."""
    return FeeRule(
        pct=Decimal("0.4"), min_rp=Decimal(5), max_rp=Decimal(2500),
        rp_kgs=rp_kgs, legal_ref=KG_RELEASE_FEE_LEGAL_REF,
    )


def _s(value: Decimal) -> str:
    return format(value.normalize(), "f") if value == value.to_integral() else format(value, "f")


def compute_percent_fee(customs_value_kgs: Decimal, rule: FeeRule) -> FeeResult:
    """0,4% от таможенной стоимости с ограничением снизу/сверху."""
    raw = customs_value_kgs * rule.pct / 100
    amount, bound = raw, None
    if raw < rule.min_kgs:
        amount, bound = rule.min_kgs, "min"
    elif raw > rule.max_kgs:
        amount, bound = rule.max_kgs, "max"
    rate_text = (
        f"{_s(rule.pct)}% от таможенной стоимости (не менее {_s(rule.min_rp)} РП = "
        f"{_s(rule.min_kgs)} сом, не более {_s(rule.max_rp)} РП = {_s(rule.max_kgs)} сом)"
    )
    return FeeResult(
        amount=amount.quantize(_CENT, rounding=ROUND_HALF_UP),
        basis=customs_value_kgs.quantize(_CENT, rounding=ROUND_HALF_UP),
        rate_text=rate_text,
        bound=bound,
    )
