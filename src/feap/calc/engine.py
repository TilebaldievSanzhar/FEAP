"""Расчёт по товару без досье: платежи и себестоимость ввоза партии.

Деньги считает тот же код, что и проект ДТ: партия превращается во временное
досье из одной позиции и проходит dt.service.assemble_and_validate (сборка
графы 47, сбор, ФЛК). Отдельной «калькуляторной» арифметики платежей нет —
расчёт и проект ДТ не могут разойтись.

Сверху — себестоимость ввоза: цена партии, фрахт до границы, таможенные
платежи и расходы вне таможенной стоимости (доставка по КР, брокер, СВХ,
сертификация), всего и на единицу. Нехватка данных → needs_input, суммы
тогда не отдаются (никаких молчаливых нулей).
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from feap.calc.schemas import (
    COST_LABELS,
    CalcIn,
    CalcOut,
    LandedCost,
    LandedCostLine,
)
from feap.db.queries import get_leaf_by_code
from feap.dt.customs_value import quantize_money
from feap.dt.exporters.xlsx import PAYMENT_KIND_RU
from feap.dt.flk import draft_status
from feap.dt.refdata import resolve_rates
from feap.refsync.schemas import RefdataOut
from feap.dt.schemas import (
    AssembledDraft,
    Finding,
    PartyIn,
    PaymentLine,
    ShipmentIn,
    ShipmentItemIn,
)
from feap.dt.service import assemble_and_validate

#: Правила ФЛК, осмысленные без досье. Стороны, документы, графы шапки и
#: классификаторы Решения 378 в расчёте не заполняются — их правила не нужны.
CALC_RULES: frozenset[str] = frozenset({
    "F-03", "F-06", "F-07", "F-08", "F-10", "F-14", "F-16", "F-17",
    "W-FREIGHT", "W-FEE", "W-FTA", "I-EAEU",
    "W-REMEDY-EXEMPT", "I-REMEDY-NA", "W-REMEDY-VAT", "W-VAT-COND", "I-VAT-COND",
    "W-REF-ATTEST", "W-REF-GUARD", "I-ETT-NOTE", "W-ETT-NOTE-END",
})

#: Стороны временного досье — в расчёт и паспорт не попадают.
_PLACEHOLDER_PARTY = PartyIn(name="—")
_DEFAULT_DESCRIPTION = "Товар (расчёт по коду)"


class CodeNotFound(LookupError):
    """Кода нет в номенклатуре или он не конечный."""


def to_shipment(calc: CalcIn) -> ShipmentIn:
    """Партия → временное досье из одной позиции (только для сборки платежей)."""
    item = ShipmentItemIn(
        description=calc.description or _DEFAULT_DESCRIPTION,
        hs_code=calc.hs_code,
        origin_country=calc.origin_country,
        qty=calc.qty,
        qty_unit=calc.qty_unit,
        price=calc.price,
        net_kg=calc.net_kg,
        gross_kg=calc.gross_kg,
        supp_qty=calc.supp_qty,
        supp_unit=calc.supp_unit,
        preference=calc.preference,
        preferential_duty_rate=calc.preferential_duty_rate,
        manufacturer=calc.manufacturer,
        remedy_decisions=calc.remedy_decisions,
        vat_benefit=calc.vat_benefit,
    )
    return ShipmentIn(
        seller=_PLACEHOLDER_PARTY,
        buyer=_PLACEHOLDER_PARTY,
        incoterms=calc.incoterms,
        contract_currency=calc.currency,
        freight_to_border=calc.freight_to_border,
        freight_beyond_border=calc.freight_beyond_border,
        insurance=calc.insurance,
        other_charges=calc.other_charges,
        rate_date=calc.rate_date,
        items=[item],
    )


def _per_unit(total: Decimal, qty: Decimal | None) -> Decimal | None:
    if not qty:
        return None
    return (total / qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def landed_cost(
    calc: CalcIn,
    *,
    contract_rate: Decimal,
    payments: list[PaymentLine],
    cost_rates: dict[str, Decimal],
) -> LandedCost:
    """Себестоимость ввоза из уже рассчитанных платежей (все amount заданы).

    Цена и фрахт до границы — в валюте счёта по курсу графы 23. Вычет
    freight_beyond_border уменьшает только таможенную стоимость: в цене этот
    фрахт уже оплачен, второй раз в себестоимость он не добавляется.
    """
    lines: list[LandedCostLine] = [
        LandedCostLine(
            kind="goods", label="Товар по счёту",
            amount=quantize_money(calc.price * contract_rate),
            source_amount=calc.price, source_currency=calc.currency,
        ),
    ]
    additions = calc.freight_to_border + calc.insurance + calc.other_charges
    if additions > 0:
        lines.append(LandedCostLine(
            kind="additions", label="Фрахт и страховка до границы ЕАЭС",
            amount=quantize_money(additions * contract_rate),
            source_amount=additions, source_currency=calc.currency,
        ))
    for p in payments:
        assert p.amount is not None, "landed_cost вызывается только без недостающих данных"
        label = PAYMENT_KIND_RU.get(p.kind, p.kind)
        if p.kind == "fee":
            label = "Сбор за таможенные операции"
        elif p.ref and p.kind not in ("duty", "vat", "excise"):
            label = f"{label} ({p.ref})"
        lines.append(LandedCostLine(
            kind=p.kind, label=label, amount=p.amount, customs_payment=True,
        ))
    for cost in calc.costs:
        rate = cost_rates[cost.currency]
        lines.append(LandedCostLine(
            kind=cost.kind, label=cost.label or COST_LABELS[cost.kind],
            amount=quantize_money(cost.amount * rate),
            source_amount=None if cost.currency == "KGS" else cost.amount,
            source_currency=None if cost.currency == "KGS" else cost.currency,
        ))

    total = sum((ln.amount for ln in lines), Decimal(0))
    vat = sum((ln.amount for ln in lines if ln.kind == "vat"), Decimal(0))
    return LandedCost(
        lines=lines,
        total=total,
        vat=vat,
        total_without_vat=total - vat,
        qty=calc.qty,
        qty_unit=calc.qty_unit,
        per_unit=_per_unit(total, calc.qty),
        per_unit_without_vat=_per_unit(total - vat, calc.qty),
    )


def calc_payments(graphs: dict) -> list[PaymentLine]:
    """Платежи позиции (графа 47) + сбор за таможенные операции — одна ДТ на партию."""
    payments = [PaymentLine.model_validate(line) for line in graphs["items"][0]["g47"]]
    fee_line = graphs["totals"].get("fee_line")
    if fee_line:
        payments.append(PaymentLine.model_validate(fee_line))
    return payments


def select_findings(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.rule in CALC_RULES]


def contract_rate_of(graphs: dict) -> Decimal | None:
    """Курс графы 23 — ровно тот, по которому собран проект (None — не подтверждён)."""
    raw = (graphs.get("header", {}).get("g23") or {}).get("rate")
    return Decimal(raw) if raw is not None else None


def refdata_out(graphs: dict, *, currencies: list[str], cost_fx: list[dict]) -> RefdataOut:
    """След справочников расчёта: курсы использованных валют + гарантии справочников."""
    meta = graphs.get("meta") or {}
    fx_meta: dict = meta.get("fx") or {}
    fx = [fx_meta[c] for c in dict.fromkeys(currencies) if c in fx_meta]
    seen = {f["currency"] for f in fx}
    fx.extend(f for f in cost_fx if f["currency"] not in seen)
    return RefdataOut.model_validate({
        "fx": fx, "datasets": (meta.get("refdata") or {}).get("datasets") or [],
    })


def build_calc_out(
    calc: CalcIn,
    assembled: AssembledDraft,
    findings: list[Finding],
    *,
    contract_rate: Decimal | None,
    cost_rates: dict[str, Decimal],
    extra_missing: list[str] | None = None,
    cost_fx: list[dict] | None = None,
) -> CalcOut:
    """Ответ расчёта из собранного временного досье (чистая функция)."""
    graphs = assembled.graphs
    item = graphs["items"][0]
    payments = calc_payments(graphs)

    missing = list(assembled.missing_parameters)
    for m in [*(p_m for p in payments for p_m in p.missing), *(extra_missing or [])]:
        if m not in missing and not any(m in known for known in missing):
            missing.append(m)

    selected = select_findings(findings)
    computed = all(p.amount is not None for p in payments)
    payments_total = (
        sum((p.amount for p in payments if p.amount is not None), Decimal(0)) if computed else None
    )
    landed = None
    if not missing and computed and contract_rate is not None:
        landed = landed_cost(
            calc, contract_rate=contract_rate, payments=payments, cost_rates=cost_rates,
        )
    g45 = item.get("g45")
    return CalcOut(
        status=draft_status(selected, missing),  # type: ignore[arg-type]
        hs_code=calc.hs_code,
        rate_date=dt.date.fromisoformat(graphs["meta"]["rate_date"]),
        currency=calc.currency,
        fx_rate=contract_rate,
        customs_value=Decimal(g45) if g45 is not None else None,
        payments=payments,
        payments_total=payments_total,
        vat_base_includes_remedies=bool(graphs["meta"].get("vat_base_includes_remedies")),
        tariff=item.get("tariff") or {},
        landed_cost=landed,
        findings=selected,
        missing_parameters=missing,
        refdata=refdata_out(graphs, currencies=[calc.currency], cost_fx=cost_fx or []),
    )


async def run_calc(session: AsyncSession, calc: CalcIn) -> CalcOut:
    """Платежи и себестоимость партии на дату. CodeNotFound — кода нет в ЕТТ."""
    if await get_leaf_by_code(session, calc.hs_code) is None:
        raise CodeNotFound(f"Код {calc.hs_code} не найден в номенклатуре или не конечный")

    assembled, findings = await assemble_and_validate(session, to_shipment(calc))
    rate_date = dt.date.fromisoformat(assembled.graphs["meta"]["rate_date"])

    # Курсы для расходов вне таможенной стоимости (валюты могут быть любыми) —
    # те же правила: только подтверждённый курс на день, иначе needs_input.
    cost_currencies = sorted({c.currency for c in calc.costs} - {"KGS"})
    values, issues, provenance, _ = await resolve_rates(
        session, currencies=cost_currencies, rate_date=rate_date,
    )
    cost_rates: dict[str, Decimal] = {"KGS": Decimal(1)}
    extra_missing: list[str] = []
    for currency in cost_currencies:
        rate = values.get(currency)
        if rate is None:
            reason = issues.get(currency)
            extra_missing.append(
                f"курс {currency} НБКР на {rate_date.isoformat()} (расходы)"
                + (f": {reason}" if reason else "")
            )
        else:
            cost_rates[currency] = rate

    return build_calc_out(
        calc, assembled, findings,
        contract_rate=contract_rate_of(assembled.graphs), cost_rates=cost_rates,
        extra_missing=extra_missing, cost_fx=list(provenance.values()),
    )
