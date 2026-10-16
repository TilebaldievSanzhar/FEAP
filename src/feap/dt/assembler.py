"""Сборка графов проекта ДТ (ИМ 40) из досье + справочного снапшота.

Выход — JSON-safe словарь `graphs` (Decimal уже строками) вида:

    {
      "meta":   {procedure, structure_version, rate_date, generated_at, ...},
      "header": {"g01": ..., "g02": ..., ..., "g25": ...},
      "items":  [{"g32": 1, "g31": {...}, "g33": ..., ..., "g47": [...],
                  "tariff": {...}}, ...],
      "totals": {"g05", "g03_sheets", "g12", "invoice_total", "payments": {...}}
    }

items[i].tariff — не графа, а след тарифных проверок для ФЛК и UI: режим
страны происхождения, меры защиты рынка и решения по ним, условные льготы НДС.

Нехватка данных для платежей/графы 46 собирается в missing_parameters
(единый механизм needs_input) — суммы никогда не занижаются молча.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from feap.dt.customs_value import (
    compute_customs_values,
    quantize_money,
    statistical_value_usd,
)
from feap.dt.duty_parser import DutyParseError, parse_duty_rate
from feap.dt.payments import (
    compute_duty,
    compute_excise,
    compute_fee,
    compute_percent_fee_line,
    compute_remedy,
    compute_vat,
)
from feap.dt.schemas import (
    NO_PREFERENCE,
    AssembledDraft,
    PaymentLine,
    PartyIn,
    ShipmentIn,
)
from feap.tariff.agreements import PREFERENTIAL_REGIMES, TradeAgreementRef, regime_for_origin
from feap.tariff.fees import FeeRule
from feap.tariff.remedies import RateChoice, RemedyMeasure, RemedyRate, find_measures, select_rate

STRUCTURE_VERSION = "257-print/1"  # печатная структура по Решению 257; XML R.055 — M3

_REMEDY_KIND_RU = {
    "antidumping": "антидемпинговая мера",
    "special": "специальная защитная мера",
    "countervailing": "компенсационная мера",
}


@dataclass(frozen=True)
class VatOption:
    """Условная льгота по НДС: применяется только по выбору декларанта."""

    source: str
    rate: str
    condition: str
    legal_ref: str
    note: str | None = None


@dataclass(frozen=True)
class ItemRef:
    """Справочные данные по коду позиции (из БД, см. refdata.build_ref_snapshot)."""

    code_exists: bool = False
    is_leaf: bool = False
    duty_raw: str | None = None
    vat_rate: str | None = None          # '12%' / '0%' — применяется без выбора
    vat_note: str | None = None          # ссылка на безусловную льготу (ст. 289 НК КР, ПКМ)
    excise_amount: Decimal | None = None
    excise_unit: str | None = None       # 'сом/литр' и т.п.
    unit_secondary: str | None = None    # требуемая доп. единица (графа 41)
    vat_options: tuple[VatOption, ...] = ()  # условные льготы (ПКМ № 816 прил. 2 и др.)
    duty_trace: dict | None = None       # откуда ставка пошлины (tariff.ett.resolve_duty)
    duty_issue: str | None = None        # ставку нельзя взять (код исключён из ЕТТ и т.п.)


@dataclass(frozen=True)
class Rates:
    """Курсы НБКР на дату расчёта (сом за единицу валюты).

    None — курс на дату не подтверждён (refsync.fx): issues объясняет почему,
    сумма тогда не считается (needs_input). provenance — след для графов и UI.
    """

    rate_date: dt.date
    contract: Decimal | None             # курс валюты контракта
    usd: Decimal | None = None
    eur: Decimal | None = None
    issues: Mapping[str, str] = field(default_factory=dict)       # валюта → причина
    provenance: Mapping[str, dict] = field(default_factory=dict)  # валюта → FxQuote.to_json()


@dataclass(frozen=True)
class DocEntry:
    """Документ досье для графы 44 (строится в documents.build_g44_entries)."""

    kind: str                            # invoice | contract | cmr | permit | ...
    code: str | None = None              # код вида документа (Решение 378, прил. 8)
    number: str | None = None
    date: str | None = None              # ISO
    permit_status: str | None = None     # статус TULPAR-проверки, если была


def _s(value: Decimal | int | float | None) -> str | None:
    """Decimal → строка для JSONB (точность не теряем)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _party(p: PartyIn | None) -> dict | None:
    if p is None:
        return None
    return {"name": p.name, "country": p.country, "address": p.address, "inn": p.inn}


def sheets_count(n_items: int) -> int:
    """Графа 3: основной лист (1 товар) + добавочные (по 3 товара)."""
    if n_items <= 1:
        return 1
    return 1 + math.ceil((n_items - 1) / 3)


def _payment_dump(line: PaymentLine) -> dict:
    return line.model_dump(mode="json")


def _doc_dump(doc: DocEntry) -> dict:
    entry: dict = {"code": doc.code, "kind": doc.kind,
                   "number": doc.number, "date": doc.date}
    if doc.permit_status is not None:
        entry["permit_status"] = doc.permit_status
    return entry


def _regime_dump(regime: TradeAgreementRef | None) -> dict | None:
    if regime is None:
        return None
    return {
        "country": regime.country,
        "regime": regime.regime,
        "agreement": regime.agreement,
        "in_force_from": regime.in_force_from.isoformat() if regime.in_force_from else None,
        "origin_proof": regime.origin_proof,
    }


def _has_preference_code(preference: str | None) -> bool:
    return bool(preference) and preference.strip().upper() not in NO_PREFERENCE


class _DutyBlocked(Exception):
    """Ставку пошлины нельзя применить (код исключён из ЕТТ, примечание не разобрано)."""


def _with_fx_reason(message: str, rates: Rates) -> str:
    """«курс EUR на дату расчёта» (специфическая ставка) + почему курса нет."""
    for currency, reason in rates.issues.items():
        if message.startswith(f"курс {currency} "):
            return f"{message}: {reason}"
    return message


def assemble(
    shipment: ShipmentIn,
    item_refs: list[ItemRef],
    rates: Rates,
    *,
    fee_kgs: Decimal | None = None,
    fee_rule: FeeRule | None = None,
    shipment_ref: str | None = None,
    documents: list[DocEntry] | None = None,
    remedies: list[RemedyMeasure] | None = None,
    agreements: list[TradeAgreementRef] | None = None,
    vat_includes_remedies: bool = False,
    refdata: dict | None = None,
) -> AssembledDraft:
    """Собрать графы проекта ДТ. len(item_refs) должен совпадать с len(items).

    fee_kgs — фиксированный сбор (ручной режим); иначе fee_rule — процентный
    сбор по п. 38 ПП КР № 79 от общей таможенной стоимости (графа 12).
    documents — документы досье для графы 44; в v1 они общие для поставки,
    поэтому один и тот же список кладётся в g44 каждого товара (практика
    заполнения общих документов ДТ).
    remedies/agreements — меры защиты рынка и торговые режимы (снапшот БД).
    refdata — след справочников (проверки гарантий, ручные курсы) → meta.refdata.
    """
    items = shipment.items
    if len(item_refs) != len(items):
        raise ValueError("item_refs должен соответствовать позициям досье 1:1")

    g44_docs = [_doc_dump(d) for d in (documents or [])]

    missing: list[str] = []
    n = len(items)
    # Без курса графы 23 таможенная стоимость неизвестна: суммы не считаются
    # вовсе (никаких молчаливых нулей), причина — в missing_parameters.
    rate_missing = rates.contract is None

    if rates.contract is None:
        reason = rates.issues.get(shipment.contract_currency.upper())
        missing.append(
            f"курс {shipment.contract_currency} НБКР на {rates.rate_date.isoformat()} (графа 23)"
            + (f": {reason}" if reason else "")
        )
        contract_rate = Decimal(0)
    else:
        contract_rate = rates.contract

    cv = compute_customs_values(
        prices=[i.price for i in items],
        contract_rate=contract_rate,
        freight_to_border=shipment.freight_to_border,
        freight_beyond_border=shipment.freight_beyond_border,
        insurance=shipment.insurance,
        other_charges=shipment.other_charges,
        allocation_method=shipment.allocation_method,
        gross_weights=[i.gross_kg for i in items],
    )

    fx = {c: r for c, r in (("USD", rates.usd), ("EUR", rates.eur)) if r is not None}
    usd_missing_reported = False

    item_graphs: list[dict] = []
    totals: dict[str, Decimal] = {"duty": Decimal(0), "excise": Decimal(0), "vat": Decimal(0)}

    for idx, (item, ref) in enumerate(zip(items, item_refs), start=1):
        cv_i = cv.customs_values[idx - 1]
        prefix = f"Товар {idx} ({(item.hs_code or 'без кода')})"
        origin = (item.origin_country or "").strip().upper() or None
        regime = regime_for_origin(agreements or [], origin, rates.rate_date)

        # ── пошлина (ЕТТ или преференциальная ставка ЗСТ) ──────────────────
        pref_raw = (item.preferential_duty_rate or "").strip()
        pref_applied = bool(
            pref_raw and _has_preference_code(item.preference)
            and regime is not None and regime.regime in PREFERENTIAL_REGIMES
        )
        duty_raw = pref_raw if pref_applied else ref.duty_raw
        try:
            if ref.duty_issue and not pref_applied:
                raise _DutyBlocked(ref.duty_issue)
            parsed = parse_duty_rate(duty_raw)
            duty = compute_duty(
                parsed, customs_value=cv_i,
                net_kg=item.net_kg, supp_qty=item.supp_qty, fx=fx,
            )
        except _DutyBlocked as exc:
            duty = PaymentLine(
                kind="duty", code="2010", basis=cv_i, rate=duty_raw or "—", amount=None,
                missing=[str(exc)],
            )
        except DutyParseError as exc:
            duty = PaymentLine(
                kind="duty", code="2010", basis=cv_i,
                rate=duty_raw or "—", amount=None,
                missing=[f"нестандартная ставка {'ЗСТ' if pref_applied else 'ЕТТ'}: {exc}"],
            )
        if pref_applied and regime is not None:
            pref_note = f"преференциальная ставка: {regime.agreement}"
            duty = duty.model_copy(update={
                "note": f"{pref_note}; {duty.note}" if duty.note else pref_note,
            })

        # ── меры защиты рынка (антидемпинг / спец. / компенс.) ─────────────
        remedy_lines: list[PaymentLine] = []
        remedies_info: list[dict] = []
        matched = find_measures(
            remedies or [], code10=item.hs_code, origin=origin, on_date=rates.rate_date,
        ) if item.hs_code else []
        for measure in matched:
            decision = item.remedy_decisions.get(measure.measure_no)
            info: dict = {
                "measure_no": measure.measure_no,
                "kind": measure.kind,
                "title": measure.title,
                "decision": decision.decision if decision else None,
                "notes": measure.notes,
            }
            if decision is None:
                missing.append(
                    f"{prefix}: {_REMEDY_KIND_RU.get(measure.kind, 'мера защиты рынка')} "
                    f"{measure.measure_no} «{measure.title}» ({origin}) совпадает по коду — "
                    f"подтвердите, подпадает ли товар под описание меры "
                    f"(remedy_decisions['{measure.measure_no}']: applies / not_applicable / exempt)"
                )
            elif decision.decision == "applies":
                if decision.rate_pct is not None:
                    choice = RateChoice(
                        RemedyRate(decision.rate_pct), note="ставка введена декларантом вручную",
                    )
                else:
                    choice = select_rate(
                        measure, origin=origin,
                        manufacturer=item.manufacturer, variant=decision.variant,
                    )
                line = compute_remedy(measure, choice, customs_value=cv_i)
                remedy_lines.append(line)
                info["rate"] = line.rate
            remedies_info.append(info)

        # ── акциз ──────────────────────────────────────────────────────────
        excise: PaymentLine | None = None
        if ref.excise_amount is not None and ref.excise_unit:
            excise = compute_excise(
                rate_amount=ref.excise_amount, rate_unit=ref.excise_unit,
                net_kg=item.net_kg, supp_qty=item.supp_qty,
            )

        # ── НДС (с условной льготой по выбору декларанта) ──────────────────
        vat_rate, vat_note, vat_ref = ref.vat_rate, ref.vat_note, None
        chosen = None
        if item.vat_benefit:
            chosen = next((o for o in ref.vat_options if o.source == item.vat_benefit), None)
        if chosen is not None:
            vat_rate = chosen.rate
            vat_note = f"{chosen.legal_ref}; условие подтверждено декларантом: {chosen.condition}"
            vat_ref = chosen.source

        remedy_total = sum((ln.amount for ln in remedy_lines if ln.amount is not None), Decimal(0))
        remedy_pending = any(ln.amount is None for ln in remedy_lines)
        excise_pending = excise is not None and excise.amount is None
        vat = compute_vat(
            customs_value=cv_i,
            duty_amount=duty.amount,
            excise_amount=excise.amount if excise is not None else Decimal(0),
            vat_rate_str=vat_rate,
            exemption_note=vat_note,
            excise_pending=excise_pending,
            extra_base=remedy_total if vat_includes_remedies else Decimal(0),
            extra_pending=remedy_pending and vat_includes_remedies,
            ref=vat_ref,
        )

        payment_lines = [duty, *remedy_lines] + ([excise] if excise else []) + [vat]
        if rate_missing:
            payment_lines = [
                ln.model_copy(update={"amount": None, "basis": None}) for ln in payment_lines
            ]
        for line in payment_lines:
            for m in line.missing:
                missing.append(f"{prefix}: {_with_fx_reason(m, rates)}")
            if line.amount is not None:
                totals[line.kind] = totals.get(line.kind, Decimal(0)) + line.amount

        # ── графа 46 ───────────────────────────────────────────────────────
        if rate_missing:
            g46 = None
        elif rates.usd is not None:
            g46 = statistical_value_usd(cv_i, rates.usd)
        else:
            g46 = None
            if not usd_missing_reported:
                reason = rates.issues.get("USD")
                missing.append(
                    f"курс USD НБКР на {rates.rate_date.isoformat()} (графа 46)"
                    + (f": {reason}" if reason else "")
                )
                usd_missing_reported = True

        item_graphs.append({
            "g32": idx,
            "g31": {
                "description": item.description,
                "qty": _s(item.qty),
                "qty_unit": item.qty_unit,
                "packages": item.packages,
                "sku": item.sku,
                "manufacturer": item.manufacturer,
            },
            "g33": item.hs_code,
            "g34": item.origin_country,
            "g35": _s(item.gross_kg),
            "g36": item.preference,
            "g38": _s(item.net_kg),
            "g41": {"qty": _s(item.supp_qty), "unit": item.supp_unit,
                    "required_unit": ref.unit_secondary},
            "g42": _s(item.price),
            "g43": "1",
            "g44": g44_docs,
            "g45": None if rate_missing else _s(cv_i),
            "g46": _s(g46),
            "g47": [_payment_dump(line) for line in payment_lines],
            "tariff": {
                "duty": ref.duty_trace,
                "origin_regime": _regime_dump(regime),
                "preferential_duty_rate": pref_raw or None,
                "preferential_rate_applied": pref_applied,
                "remedies": remedies_info,
                "vat_options": [
                    {"source": o.source, "rate": o.rate, "condition": o.condition,
                     "legal_ref": o.legal_ref}
                    for o in ref.vat_options
                ],
                "vat_benefit": item.vat_benefit,
                "vat_benefit_applied": chosen is not None,
            },
        })

    invoice_total = quantize_money(sum((i.price for i in items), Decimal(0)))
    g12_total = quantize_money(sum(cv.customs_values, Decimal(0))) if n else Decimal("0.00")

    # ── сбор за оформление (одна строка на ДТ) ──────────────────────────────
    fee_line: PaymentLine | None = None
    if fee_kgs is not None:
        fee_line = compute_fee(fee_kgs)
    elif fee_rule is not None and n and rates.contract is not None:
        fee_line = compute_percent_fee_line(g12_total, fee_rule)
    if fee_line is not None and fee_line.amount is not None:
        totals["fee"] = fee_line.amount

    declarant = shipment.declarant or shipment.buyer
    places_total = shipment.places_total
    if places_total is None:
        per_item = [i.packages for i in items]
        places_total = sum(p for p in per_item if p) if any(per_item) else None

    header = {
        "g01": {"direction": "ИМ", "procedure": "40", "form": "ЭД"},
        "g02": _party(shipment.seller),
        "g03": {"sheets": sheets_count(n)},
        "g05": n,
        "g06": places_total,
        "g07": shipment_ref,
        "g08": _party(shipment.buyer),
        "g09": _party(shipment.buyer),
        "g11": shipment.trading_country,
        "g12": None if rate_missing else _s(g12_total),
        "g14": _party(declarant),
        "g15": shipment.dispatch_country,
        "g17": shipment.destination_country,
        "g18": shipment.transport_ids,
        "g19": None if shipment.container is None else ("1" if shipment.container else "0"),
        "g20": {"incoterms": shipment.incoterms, "place": shipment.incoterms_place},
        "g21": shipment.transport_ids,
        "g22": {"currency": shipment.contract_currency, "amount": _s(invoice_total)},
        "g23": {"rate": _s(rates.contract), "date": rates.rate_date.isoformat()},
        "g24": shipment.deal_nature,
        "g25": shipment.transport_at_border_type,
        "g37": {"requested": "40", "previous": "00", "feature": "000"},
    }

    graphs = {
        "meta": {
            "procedure": shipment.procedure,
            "structure_version": STRUCTURE_VERSION,
            "rate_date": rates.rate_date.isoformat(),
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "vat_base_includes_remedies": vat_includes_remedies,
            "fee_mode": "fixed" if fee_kgs is not None else ("percent" if fee_rule else "off"),
            "fx": {cur: dict(p) for cur, p in rates.provenance.items()},
            "refdata": refdata or {},
        },
        "header": header,
        "items": item_graphs,
        "totals": {
            "g05": n,
            "g03_sheets": sheets_count(n),
            "g12": None if rate_missing else _s(g12_total),
            "invoice_total": _s(invoice_total),
            "additions_total": None if rate_missing else _s(cv.additions_total),
            "payments": {} if rate_missing else {
                k: _s(quantize_money(v)) for k, v in totals.items()
            },
            "fee_line": _payment_dump(fee_line) if fee_line else None,
        },
    }
    return AssembledDraft(graphs=graphs, missing_parameters=missing)
