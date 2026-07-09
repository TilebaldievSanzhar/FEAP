"""Графа 47: исчисление платежей (сбор, пошлина, акциз, НДС) для ИМ 40.

Чистые функции на Decimal. Нехватка физической базы (вес/доп. единица)
никогда не превращается в ноль — она возвращается в PaymentLine.missing
и поднимается оркестратором как missing_parameters (механизм needs_input).

НДС КР: база = таможенная стоимость + пошлина + акциз (НК КР).
"""

from __future__ import annotations

import re
from decimal import Decimal

from feap.dt.customs_value import quantize_money
from feap.dt.duty_parser import (
    BASIS_MISSING_LABEL,
    BasisKind,
    DutyKind,
    ParsedDuty,
)
from feap.dt.schemas import PAYMENT_CODES, PaymentLine

_PCT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*%$")


def parse_pct(rate_str: str | None) -> Decimal | None:
    """'12%' → Decimal('12'). None/пусто/не-процент → None."""
    if not rate_str:
        return None
    m = _PCT_RE.match(rate_str.strip())
    return Decimal(m.group(1)) if m else None


def _basis_quantity(
    basis: BasisKind,
    *,
    net_kg: Decimal | None,
    supp_qty: Decimal | None,
) -> tuple[Decimal | None, str | None]:
    """Количество в единицах базы ставки, либо (None, label_недостающего)."""
    label = BASIS_MISSING_LABEL[basis]
    if basis in (BasisKind.NET_KG, BasisKind.NET_TON):
        if net_kg is None:
            return None, label
        return (net_kg if basis is BasisKind.NET_KG else net_kg / Decimal(1000)), None
    if supp_qty is None:
        return None, label
    if basis is BasisKind.THOUSAND_PIECES:
        return supp_qty / Decimal(1000), None
    return supp_qty, None


def compute_duty(
    parsed: ParsedDuty,
    *,
    customs_value: Decimal,
    net_kg: Decimal | None,
    supp_qty: Decimal | None,
    fx: dict[str, Decimal],
) -> PaymentLine:
    """Ввозная пошлина по разобранной ставке ЕТТ. fx: {'EUR': ..., 'USD': ...} (сом)."""
    line = PaymentLine(
        kind="duty", code=PAYMENT_CODES["duty"],
        basis=customs_value, rate=parsed.raw or "—", amount=None,
    )
    if parsed.kind is DutyKind.NONE:
        return line.model_copy(update={"amount": Decimal("0.00"), "rate": "0 (ставка не задана)"})

    advalorem: Decimal | None = None
    if parsed.advalorem_pct is not None:
        advalorem = quantize_money(customs_value * parsed.advalorem_pct / 100)

    specific: Decimal | None = None
    missing: list[str] = []
    if parsed.has_specific:
        assert parsed.basis is not None and parsed.specific_currency is not None
        qty, missing_label = _basis_quantity(parsed.basis, net_kg=net_kg, supp_qty=supp_qty)
        cur_rate = fx.get(parsed.specific_currency)
        if missing_label:
            missing.append(missing_label)
        if cur_rate is None:
            missing.append(f"курс {parsed.specific_currency} на дату расчёта")
        if qty is not None and cur_rate is not None:
            specific = quantize_money(qty * parsed.specific_amount * cur_rate)

    if parsed.kind is DutyKind.AD_VALOREM:
        return line.model_copy(update={"amount": advalorem})

    if missing:
        # Для combined_min без специфической части нельзя определить max —
        # честно останавливаемся, а не занижаем платёж.
        return line.model_copy(update={"missing": missing})

    if parsed.kind is DutyKind.SPECIFIC:
        return line.model_copy(update={"amount": specific})
    if parsed.kind is DutyKind.COMBINED_MIN:
        assert advalorem is not None and specific is not None
        applied = "адвалорная" if advalorem >= specific else "специфическая (не менее)"
        return line.model_copy(update={"amount": max(advalorem, specific), "note": f"применена {applied} часть"})
    # COMBINED_PLUS
    assert advalorem is not None and specific is not None
    return line.model_copy(update={"amount": quantize_money(advalorem + specific)})


# ── Акциз ────────────────────────────────────────────────────────────────────

#: rate_unit из excise_rates → база количества.
_EXCISE_UNIT_TO_BASIS: dict[str, BasisKind] = {
    "сом/кг": BasisKind.NET_KG,
    "сом/тонну": BasisKind.NET_TON,
    "сом/литр": BasisKind.LITRE,
    "сом/шт": BasisKind.PIECE,
    "сом/1000 шт": BasisKind.THOUSAND_PIECES,
    "сом/см3": BasisKind.CM3,
}


def compute_excise(
    *,
    rate_amount: Decimal,
    rate_unit: str,
    net_kg: Decimal | None,
    supp_qty: Decimal | None,
) -> PaymentLine:
    """Акциз КР (ст. 336 НК КР): специфические ставки «сом за единицу»."""
    line = PaymentLine(
        kind="excise", code=PAYMENT_CODES["excise"],
        basis=None, rate=f"{rate_amount} {rate_unit}", amount=None,
    )
    unit = rate_unit.strip().lower().replace(" ", " ")
    basis = _EXCISE_UNIT_TO_BASIS.get(unit)
    if basis is None:
        return line.model_copy(update={
            "missing": [f"неподдерживаемая единица акциза: {rate_unit!r} — требуется ручной расчёт"],
        })
    qty, missing_label = _basis_quantity(basis, net_kg=net_kg, supp_qty=supp_qty)
    if missing_label:
        return line.model_copy(update={"missing": [missing_label]})
    assert qty is not None
    return line.model_copy(update={"basis": qty, "amount": quantize_money(qty * rate_amount)})


# ── НДС ──────────────────────────────────────────────────────────────────────

def compute_vat(
    *,
    customs_value: Decimal,
    duty_amount: Decimal | None,
    excise_amount: Decimal | None,
    vat_rate_str: str | None,
    exemption_note: str | None = None,
    excise_pending: bool = False,
) -> PaymentLine:
    """НДС на импорт: база = ТС + пошлина + акциз. Ставка приходит строкой ('12%' / '0%').

    Если пошлина/акциз ещё не рассчитаны (missing у их строк) — НДС тоже
    не считается: база была бы занижена.
    """
    pct = parse_pct(vat_rate_str)
    line = PaymentLine(
        kind="vat", code=PAYMENT_CODES["vat"],
        basis=None, rate=vat_rate_str or "—", amount=None, note=exemption_note,
    )
    if pct is None:
        return line.model_copy(update={"missing": [f"не удалось разобрать ставку НДС: {vat_rate_str!r}"]})

    missing: list[str] = []
    if duty_amount is None:
        missing.append("сумма пошлины (не рассчитана)")
    if excise_pending:
        missing.append("сумма акциза (не рассчитана)")
    if missing:
        return line.model_copy(update={"missing": missing})

    base = customs_value + (duty_amount or Decimal(0)) + (excise_amount or Decimal(0))
    return line.model_copy(update={"basis": quantize_money(base), "amount": quantize_money(base * pct / 100)})


# ── Сбор за таможенное оформление ───────────────────────────────────────────

def compute_fee(fee_kgs: Decimal | None) -> PaymentLine | None:
    """Сбор за оформление: пока фикс из настроек (TODO verify НПА КР).

    None/не задан → строка сбора не формируется (ФЛК добавит warning W-FEE).
    """
    if fee_kgs is None or fee_kgs <= 0:
        return None
    return PaymentLine(
        kind="fee", code=PAYMENT_CODES["fee"],
        basis=None, rate=f"{fee_kgs} сом (фикс.)", amount=quantize_money(fee_kgs),
        note="ставка сбора задана настройкой DT_CUSTOMS_FEE_KGS — сверить с НПА КР",
    )
