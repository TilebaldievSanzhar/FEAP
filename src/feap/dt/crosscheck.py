"""Кросс-сверка досье с извлечёнными документами.

Разделение труда (dt_core_dev_plan.md §4):
- ЧИСЛА сверяет только код: правила X-01..X-06 — чистая функция
  numeric_findings(), детерминированная, вызывается и в generate_draft.
- СУЩНОСТИ (наименования сторон, строки инвойса ↔ позиции) нечётко
  сопоставляет LLM через схему с match_confidence; ниже порога —
  «не сопоставлено», а не догадка.

Severity числовых правил — warning: извлечённые LLM данные документов
не могут блокировать статус ready детерминированного драфта; финальное
слово за человеком.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from feap.config import settings
from feap.dt.extractors.checks import parse_num
from feap.dt.schemas import CrosscheckOut, Finding, ShipmentIn
from feap.llm.client import classify as llm_classify
from feap.prompts.registry import render

#: Допуск расхождения денег, % (синхронно с ФЛК F-04).
MONEY_TOLERANCE_PCT = Decimal("0.5")
#: Допуск расхождения весов, % — упаковка и округления дают больший разброс.
WEIGHT_TOLERANCE_PCT = Decimal("2")


def _doc_label(payload: dict) -> str:
    doc = payload.get("doc") or {}
    kind = payload.get("kind") or payload.get("row_kind") or "документ"
    number = doc.get("number")
    return f"{kind} №{number}" if number else str(kind)


def _diverges(expected: Decimal, actual: Decimal, tolerance_pct: Decimal) -> bool:
    if expected == 0:
        return actual != 0
    return abs(actual - expected) / abs(expected) * 100 > tolerance_pct


def _fmt(value: Decimal) -> str:
    return format(value, "f")


def numeric_findings(model: ShipmentIn, docs: list[dict]) -> list[Finding]:
    """Правила X-01..X-06 — только арифметика и точные сравнения, без LLM."""
    findings: list[Finding] = []

    def add(rule: str, message: str, **details: object) -> None:
        findings.append(Finding(
            rule=rule, severity="warning", graph=None, message=message,
            details={k: str(v) for k, v in details.items()},
        ))

    items_price_sum = sum((i.price for i in model.items), Decimal(0))
    items_net = [i.net_kg for i in model.items]
    items_gross = [i.gross_kg for i in model.items]
    net_sum = sum((w for w in items_net if w is not None), Decimal(0))
    gross_sum = sum((w for w in items_gross if w is not None), Decimal(0))
    net_complete = all(w is not None for w in items_net) and model.items
    gross_complete = all(w is not None for w in items_gross) and model.items

    for payload in docs:
        doc = payload.get("doc") or {}
        kind = payload.get("kind")
        label = _doc_label(payload)

        if kind == "invoice":
            # ── X-01: итог инвойса vs Σ цен позиций ────────────────────────
            total = parse_num(doc.get("total_amount"))
            if total is not None and model.items:
                if _diverges(total, items_price_sum, MONEY_TOLERANCE_PCT):
                    add("X-01",
                        f"Итог {label} ({_fmt(total)}) расходится с суммой цен "
                        f"позиций досье ({_fmt(items_price_sum)})",
                        doc=label, expected=_fmt(total), actual=_fmt(items_price_sum))

            # ── X-02: валюта инвойса vs графа 22 ───────────────────────────
            currency = doc.get("currency")
            if currency and currency != model.contract_currency:
                add("X-02",
                    f"Валюта {label} ({currency}) не совпадает с валютой "
                    f"досье ({model.contract_currency})",
                    doc=label, expected=model.contract_currency, actual=currency)

            # ── X-06: Incoterms инвойса vs графа 20 ────────────────────────
            incoterms = doc.get("incoterms")
            if incoterms and incoterms.upper() != model.incoterms:
                add("X-06",
                    f"Базис поставки в {label} ({incoterms}) не совпадает "
                    f"с досье ({model.incoterms})",
                    doc=label, expected=model.incoterms, actual=incoterms)

        elif kind == "packing":
            # ── X-03: итого нетто vs Σ нетто позиций ───────────────────────
            doc_net = parse_num(doc.get("net_total_kg"))
            if doc_net is not None and net_complete:
                if _diverges(doc_net, net_sum, WEIGHT_TOLERANCE_PCT):
                    add("X-03",
                        f"Нетто по {label} ({_fmt(doc_net)} кг) расходится "
                        f"с Σ нетто позиций ({_fmt(net_sum)} кг)",
                        doc=label, expected=_fmt(doc_net), actual=_fmt(net_sum))

            # ── X-04: итого брутто и места ─────────────────────────────────
            doc_gross = parse_num(doc.get("gross_total_kg"))
            if doc_gross is not None and gross_complete:
                if _diverges(doc_gross, gross_sum, WEIGHT_TOLERANCE_PCT):
                    add("X-04",
                        f"Брутто по {label} ({_fmt(doc_gross)} кг) расходится "
                        f"с Σ брутто позиций ({_fmt(gross_sum)} кг)",
                        doc=label, expected=_fmt(doc_gross), actual=_fmt(gross_sum))
            doc_places = doc.get("packages_total")
            if doc_places is not None and model.places_total is not None:
                if int(doc_places) != model.places_total:
                    add("X-04",
                        f"Мест по {label} ({doc_places}) ≠ графе 6 досье "
                        f"({model.places_total})",
                        doc=label, expected=model.places_total, actual=doc_places)

        elif kind == "cmr":
            # ── X-05: брутто CMR vs Σ брутто позиций ───────────────────────
            cmr_gross = parse_num(doc.get("gross_kg"))
            if cmr_gross is not None and gross_complete:
                if _diverges(cmr_gross, gross_sum, WEIGHT_TOLERANCE_PCT):
                    add("X-05",
                        f"Брутто по {label} ({_fmt(cmr_gross)} кг) расходится "
                        f"с Σ брутто позиций ({_fmt(gross_sum)} кг)",
                        doc=label, expected=_fmt(cmr_gross), actual=_fmt(gross_sum))

        elif kind == "contract":
            # ── X-02: валюта контракта ─────────────────────────────────────
            currency = doc.get("currency")
            if currency and currency != model.contract_currency:
                add("X-02",
                    f"Валюта {label} ({currency}) не совпадает с валютой "
                    f"досье ({model.contract_currency})",
                    doc=label, expected=model.contract_currency, actual=currency)

            # ── X-06: Incoterms контракта ──────────────────────────────────
            incoterms = doc.get("incoterms")
            if incoterms and incoterms.upper() != model.incoterms:
                add("X-06",
                    f"Базис поставки в {label} ({incoterms}) не совпадает "
                    f"с досье ({model.incoterms})",
                    doc=label, expected=model.incoterms, actual=incoterms)

    return findings


# ── LLM: нечёткое сопоставление сущностей (уровень 92) ──────────────────────

class PartyMatch(BaseModel):
    pair_id: str = Field(description="id пары из входных данных")
    verdict: Literal["match", "mismatch", "uncertain"]
    match_confidence: float = Field(ge=0, le=1)
    reasoning: str = Field(description="Кратко: почему (транслитерация, форма собственности...)")


class LineMatch(BaseModel):
    doc_line_no: int = Field(description="Номер строки документа")
    shipment_position: int | None = Field(
        description="Номер позиции досье или null, если пары нет"
    )
    match_confidence: float = Field(ge=0, le=1)
    reasoning: str


class EntityMatchResult(BaseModel):
    comparison_review: str = Field(
        description="Шаг 1: общий обзор — какие сущности сопоставляются, что бросается в глаза"
    )
    parties: list[PartyMatch] = Field(default_factory=list)
    invoice_lines: list[LineMatch] = Field(default_factory=list)


#: kind → (поле документа, сторона досье)
_PARTY_MAP: dict[str, list[tuple[str, str]]] = {
    "invoice": [("seller", "seller"), ("buyer", "buyer")],
    "contract": [("seller", "seller"), ("buyer", "buyer")],
    "cmr": [("sender", "seller"), ("receiver", "buyer")],
    "cert_origin": [("exporter", "seller"), ("importer", "buyer")],
}

_SIDE_LABELS = {"seller": "продавец (графа 2)", "buyer": "покупатель (графа 8)"}


def build_party_pairs(model: ShipmentIn, docs: list[dict]) -> list[dict]:
    """Пары «сторона досье ↔ сторона документа» для нечёткой сверки."""
    pairs: list[dict] = []
    for payload in docs:
        doc = payload.get("doc") or {}
        kind = payload.get("kind") or ""
        for doc_field, side in _PARTY_MAP.get(kind, []):
            party = doc.get(doc_field) or {}
            doc_name = party.get("name")
            model_party = getattr(model, side, None)
            if not doc_name or model_party is None:
                continue
            pairs.append({
                "pair_id": f"{payload.get('doc_id', kind)}:{doc_field}",
                "entity": _SIDE_LABELS[side],
                "doc_label": _doc_label(payload),
                "doc_value": ", ".join(
                    str(v) for v in (doc_name, party.get("country"), party.get("inn")) if v
                ),
                "shipment_value": ", ".join(
                    str(v) for v in (model_party.name, model_party.country, model_party.inn) if v
                ),
            })
    return pairs


def build_line_inputs(model: ShipmentIn, docs: list[dict]) -> list[dict]:
    """Строки инвойсов для сопоставления с позициями досье."""
    lines: list[dict] = []
    for payload in docs:
        if payload.get("kind") != "invoice":
            continue
        doc = payload.get("doc") or {}
        for line in doc.get("lines") or []:
            lines.append({
                "doc_label": _doc_label(payload),
                "line_no": line.get("line_no"),
                "description": line.get("description"),
                "sku": line.get("sku"),
                "qty": line.get("qty"),
            })
    return lines


async def match_entities(model: ShipmentIn, docs: list[dict]) -> dict:
    """LLM-сопоставление с harness-валидацией результата."""
    pairs = build_party_pairs(model, docs)
    lines = build_line_inputs(model, docs)
    if not pairs and not lines:
        return {"parties": [], "invoice_lines": [], "dropped": [], "llm_used": False}

    items = [
        {"position": idx, "description": item.description, "sku": item.sku}
        for idx, item in enumerate(model.items, start=1)
    ]
    system_prompt = render("system/dt_crosscheck.jinja2")
    user_message = render(
        "context/dt_crosscheck_input.jinja2",
        pairs=pairs, lines=lines, items=items,
    )
    result = await llm_classify(
        level=92,
        system_prompt=system_prompt,
        user_message=user_message,
        output_model=EntityMatchResult,
    )

    # ── Harness: чужие id отбрасываем, неуверенное — «не сопоставлено» ──────
    dropped: list[str] = []
    threshold = settings.DT_MATCH_CONFIDENCE_MIN
    known_pairs = {p["pair_id"]: p for p in pairs}
    valid_positions = {i["position"] for i in items}
    known_lines = {(ln["line_no"]) for ln in lines}

    parties_out: list[dict] = []
    for match in result.parties:
        pair = known_pairs.get(match.pair_id)
        if pair is None:
            dropped.append(f"parties: неизвестный pair_id «{match.pair_id}»")
            continue
        verdict = match.verdict
        if match.match_confidence < threshold and verdict != "uncertain":
            dropped.append(
                f"parties {match.pair_id}: verdict «{verdict}» с confidence "
                f"{match.match_confidence:.2f} < {threshold} → uncertain"
            )
            verdict = "uncertain"
        parties_out.append({
            **pair,
            "verdict": verdict,
            "match_confidence": match.match_confidence,
            "reasoning": match.reasoning,
        })

    lines_out: list[dict] = []
    for line_match in result.invoice_lines:
        if line_match.doc_line_no not in known_lines:
            dropped.append(f"lines: неизвестная строка №{line_match.doc_line_no}")
            continue
        position = line_match.shipment_position
        if position is not None and position not in valid_positions:
            dropped.append(
                f"lines №{line_match.doc_line_no}: позиция {position} не существует → null"
            )
            position = None
        if position is not None and line_match.match_confidence < threshold:
            dropped.append(
                f"lines №{line_match.doc_line_no}: confidence "
                f"{line_match.match_confidence:.2f} < {threshold} → не сопоставлено"
            )
            position = None
        lines_out.append({
            "doc_line_no": line_match.doc_line_no,
            "shipment_position": position,
            "match_confidence": line_match.match_confidence,
            "reasoning": line_match.reasoning,
        })

    return {
        "parties": parties_out,
        "invoice_lines": lines_out,
        "dropped": dropped,
        "llm_used": True,
    }


async def run_crosscheck(model: ShipmentIn, docs: list[dict]) -> CrosscheckOut:
    """Полная кросс-сверка: числа (код) + сущности (LLM)."""
    findings = numeric_findings(model, docs)
    matches = await match_entities(model, docs)
    return CrosscheckOut(
        findings=findings,
        parties=matches["parties"],
        invoice_lines=matches["invoice_lines"],
        dropped=matches["dropped"],
        llm_used=matches["llm_used"],
    )
