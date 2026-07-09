"""Сборка графов проекта ДТ (ИМ 40) из досье + справочного снапшота.

Выход — JSON-safe словарь `graphs` (Decimal уже строками) вида:

    {
      "meta":   {procedure, structure_version, rate_date, generated_at},
      "header": {"g01": ..., "g02": ..., ..., "g25": ...},
      "items":  [{"g32": 1, "g31": {...}, "g33": ..., ..., "g47": [...]}, ...],
      "totals": {"g05", "g03_sheets", "g12", "invoice_total", "payments": {...}}
    }

Нехватка данных для платежей/графы 46 собирается в missing_parameters
(единый механизм needs_input) — суммы никогда не занижаются молча.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from decimal import Decimal

from feap.dt.customs_value import (
    compute_customs_values,
    quantize_money,
    statistical_value_usd,
)
from feap.dt.duty_parser import DutyParseError, parse_duty_rate
from feap.dt.payments import compute_duty, compute_excise, compute_fee, compute_vat
from feap.dt.schemas import (
    AssembledDraft,
    PaymentLine,
    PartyIn,
    ShipmentIn,
)

STRUCTURE_VERSION = "257-print/1"  # печатная структура по Решению 257; XML R.055 — M3


@dataclass(frozen=True)
class ItemRef:
    """Справочные данные по коду позиции (из БД, см. refdata.build_ref_snapshot)."""

    code_exists: bool = False
    is_leaf: bool = False
    duty_raw: str | None = None
    vat_rate: str | None = None          # '12%' / '0%'
    vat_note: str | None = None          # ссылка на освобождение (ст. 289 НК КР)
    excise_amount: Decimal | None = None
    excise_unit: str | None = None       # 'сом/литр' и т.п.
    unit_secondary: str | None = None    # требуемая доп. единица (графа 41)


@dataclass(frozen=True)
class Rates:
    """Курсы НБКР на дату расчёта (сом за единицу валюты)."""

    rate_date: dt.date
    contract: Decimal | None             # курс валюты контракта
    usd: Decimal | None = None
    eur: Decimal | None = None


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


def assemble(
    shipment: ShipmentIn,
    item_refs: list[ItemRef],
    rates: Rates,
    *,
    fee_kgs: Decimal | None = None,
    shipment_ref: str | None = None,
    documents: list[DocEntry] | None = None,
) -> AssembledDraft:
    """Собрать графы проекта ДТ. len(item_refs) должен совпадать с len(items).

    documents — документы досье для графы 44; в v1 они общие для поставки,
    поэтому один и тот же список кладётся в g44 каждого товара (практика
    заполнения общих документов ДТ).
    """
    items = shipment.items
    if len(item_refs) != len(items):
        raise ValueError("item_refs должен соответствовать позициям досье 1:1")

    g44_docs = [_doc_dump(d) for d in (documents or [])]

    missing: list[str] = []
    n = len(items)

    if rates.contract is None:
        missing.append(
            f"курс {shipment.contract_currency} НБКР на {rates.rate_date.isoformat()} (графа 23)"
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
    totals = {"duty": Decimal(0), "excise": Decimal(0), "vat": Decimal(0)}

    for idx, (item, ref) in enumerate(zip(items, item_refs), start=1):
        cv_i = cv.customs_values[idx - 1]
        prefix = f"Товар {idx} ({(item.hs_code or 'без кода')})"

        # ── пошлина ────────────────────────────────────────────────────────
        try:
            parsed = parse_duty_rate(ref.duty_raw)
            duty = compute_duty(
                parsed, customs_value=cv_i,
                net_kg=item.net_kg, supp_qty=item.supp_qty, fx=fx,
            )
        except DutyParseError as exc:
            duty = PaymentLine(
                kind="duty", code="2010", basis=cv_i,
                rate=ref.duty_raw or "—", amount=None,
                missing=[f"нестандартная ставка ЕТТ: {exc}"],
            )

        # ── акциз ──────────────────────────────────────────────────────────
        excise: PaymentLine | None = None
        if ref.excise_amount is not None and ref.excise_unit:
            excise = compute_excise(
                rate_amount=ref.excise_amount, rate_unit=ref.excise_unit,
                net_kg=item.net_kg, supp_qty=item.supp_qty,
            )

        # ── НДС ────────────────────────────────────────────────────────────
        excise_pending = excise is not None and excise.amount is None
        vat = compute_vat(
            customs_value=cv_i,
            duty_amount=duty.amount,
            excise_amount=excise.amount if excise is not None else Decimal(0),
            vat_rate_str=ref.vat_rate,
            exemption_note=ref.vat_note,
            excise_pending=excise_pending,
        )

        payment_lines = [duty] + ([excise] if excise else []) + [vat]
        for line in payment_lines:
            for m in line.missing:
                missing.append(f"{prefix}: {m}")
            if line.amount is not None:
                totals[line.kind] = totals.get(line.kind, Decimal(0)) + line.amount

        # ── графа 46 ───────────────────────────────────────────────────────
        if rates.usd is not None:
            g46 = statistical_value_usd(cv_i, rates.usd)
        else:
            g46 = None
            if not usd_missing_reported:
                missing.append(
                    f"курс USD НБКР на {rates.rate_date.isoformat()} (графа 46)"
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
            "g45": _s(cv_i),
            "g46": _s(g46),
            "g47": [_payment_dump(line) for line in payment_lines],
        })

    # ── сбор за оформление (одна строка на ДТ) ──────────────────────────────
    fee_line = compute_fee(fee_kgs)
    if fee_line is not None and fee_line.amount is not None:
        totals["fee"] = fee_line.amount

    invoice_total = quantize_money(sum((i.price for i in items), Decimal(0)))
    g12_total = quantize_money(sum(cv.customs_values, Decimal(0))) if n else Decimal("0.00")
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
        "g12": _s(g12_total),
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
        },
        "header": header,
        "items": item_graphs,
        "totals": {
            "g05": n,
            "g03_sheets": sheets_count(n),
            "g12": _s(g12_total),
            "invoice_total": _s(invoice_total),
            "additions_total": _s(cv.additions_total),
            "payments": {k: _s(quantize_money(v)) for k, v in totals.items()},
            "fee_line": _payment_dump(fee_line) if fee_line else None,
        },
    }
    return AssembledDraft(graphs=graphs, missing_parameters=missing)
