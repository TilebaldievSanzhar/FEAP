"""Таможенная стоимость (метод 1): Incoterms-логика, распределение расходов, графы 45/46.

Все суммы — Decimal. Округление — до 0.01 сома методом наибольших остатков,
так что сумма распределённых долей всегда равна распределяемой сумме.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from feap.dt.schemas import AllocationMethod, Incoterms

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class IncotermsProfile:
    """Что уже включено в цену продавца при данном базисе поставки."""

    freight_in_price: bool
    insurance_in_price: bool


#: Матрица Incoterms 2020 для расчёта таможенной стоимости.
#: E/F-группы: фрахт до границы ЕАЭС НЕ в цене — добавляется (графа 45).
#: C/D-группы: основной фрахт в цене; возможен вычет пост-граничной части
#: (freight_beyond_border), если она документально выделена.
INCOTERMS_PROFILES: dict[str, IncotermsProfile] = {
    "EXW": IncotermsProfile(freight_in_price=False, insurance_in_price=False),
    "FCA": IncotermsProfile(freight_in_price=False, insurance_in_price=False),
    "FAS": IncotermsProfile(freight_in_price=False, insurance_in_price=False),
    "FOB": IncotermsProfile(freight_in_price=False, insurance_in_price=False),
    "CFR": IncotermsProfile(freight_in_price=True, insurance_in_price=False),
    "CPT": IncotermsProfile(freight_in_price=True, insurance_in_price=False),
    "CIF": IncotermsProfile(freight_in_price=True, insurance_in_price=True),
    "CIP": IncotermsProfile(freight_in_price=True, insurance_in_price=True),
    "DAP": IncotermsProfile(freight_in_price=True, insurance_in_price=False),
    "DPU": IncotermsProfile(freight_in_price=True, insurance_in_price=False),
    "DDP": IncotermsProfile(freight_in_price=True, insurance_in_price=False),
}

#: Базисы, при которых нулевой фрахт до границы подозрителен (правило W-FREIGHT).
FREIGHT_EXPECTED: frozenset[str] = frozenset({"EXW", "FCA", "FAS", "FOB"})


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def allocate(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Распределить сумму по весам с округлением до 0.01 (largest remainder).

    Гарантия: sum(result) == quantize_money(total). Нулевые/пустые веса —
    равное распределение.
    """
    n = len(weights)
    if n == 0:
        return []
    total = quantize_money(total)
    if total == 0:
        return [Decimal("0.00")] * n

    weight_sum = sum(weights)
    if weight_sum <= 0:
        weights = [Decimal(1)] * n
        weight_sum = Decimal(n)

    raw = [total * w / weight_sum for w in weights]
    floored = [r.quantize(_CENT, rounding="ROUND_FLOOR") for r in raw]
    remainder = total - sum(floored)
    # Раздаём копейки тем, у кого наибольшая дробная часть.
    cents = int((remainder / _CENT).to_integral_value())
    order = sorted(range(n), key=lambda i: raw[i] - floored[i], reverse=True)
    result = list(floored)
    for i in order[:cents]:
        result[i] += _CENT
    return result


@dataclass(frozen=True)
class CustomsValueResult:
    customs_values: list[Decimal]       # графа 45 по позициям, сом
    additions_total: Decimal            # распределённые доначисления, сом
    prices_kgs: list[Decimal]           # цены позиций в сомах (до доначислений)


def compute_customs_values(
    *,
    prices: list[Decimal],
    contract_rate: Decimal,
    freight_to_border: Decimal = Decimal(0),
    freight_beyond_border: Decimal = Decimal(0),
    insurance: Decimal = Decimal(0),
    other_charges: Decimal = Decimal(0),
    allocation_method: AllocationMethod = "gross_weight",
    gross_weights: list[Decimal | None] | None = None,
) -> CustomsValueResult:
    """Графа 45 по позициям.

    Все входные суммы — в валюте контракта; contract_rate — сом за единицу валюты.
    freight_beyond_border вычитается (пост-граничная часть фрахта, если выделена).
    """
    n = len(prices)
    prices_kgs = [quantize_money(p * contract_rate) for p in prices]

    additions_ccy = freight_to_border + insurance + other_charges - freight_beyond_border
    additions_kgs = quantize_money(additions_ccy * contract_rate)

    if allocation_method == "gross_weight" and gross_weights is not None:
        # Позиции без веса участвуют с нулевым весом; если веса нет ни у кого —
        # allocate() сам перейдёт на равное распределение.
        basis = [w if w is not None else Decimal(0) for w in gross_weights]
        if sum(basis) <= 0:
            basis = list(prices)
    else:
        basis = list(prices)

    shares = allocate(additions_kgs, basis) if n else []
    customs_values = [quantize_money(prices_kgs[i] + shares[i]) for i in range(n)]
    return CustomsValueResult(
        customs_values=customs_values,
        additions_total=additions_kgs,
        prices_kgs=prices_kgs,
    )


def statistical_value_usd(customs_value_kgs: Decimal, usd_rate: Decimal) -> Decimal:
    """Графа 46: таможенная стоимость в USD."""
    if usd_rate <= 0:
        raise ValueError("Курс USD должен быть положительным")
    return quantize_money(customs_value_kgs / usd_rate)


def incoterms_profile(term: Incoterms | str) -> IncotermsProfile:
    return INCOTERMS_PROFILES[str(term)]
