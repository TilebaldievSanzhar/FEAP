"""Findings-fixer: LLM предлагает исправления, ядро их верифицирует, человек применяет.

Routing-схема (SGR): action = propose_value | ask_user | cannot_fix.
Схема сознательно плоская (Literal-дискриминатор + опциональные поля), а не
Union: anyOf в strict json_schema поддержан не всеми провайдерами OpenRouter;
маршрутизация от этого не страдает — dispatch делает код по action.

Harness-контроль (dt_core_dev_plan.md §4):
- field_path проверяется по whitelist редактируемых полей досье;
- значение коэрсится pydantic'ом (мусор → предложение помечается невалидным);
- патч применяется к КОПИИ модели и прогоняется через ту же сборку+ФЛК,
  что и generate_draft (service.assemble_and_validate): verified=True только
  если целевой finding исчез и новых error не появилось;
- ничего не сохраняется — применяет человек обычным PUT досье.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from feap.dt.flk import draft_status
from feap.dt.schemas import Finding, FixOut, ShipmentIn
from feap.llm.client import classify as llm_classify
from feap.prompts.registry import render

#: Редактируемые поля шапки досье (whitelist; всё остальное — отказ).
HEADER_FIELDS: frozenset[str] = frozenset({
    "freight_to_border", "freight_beyond_border", "insurance", "other_charges",
    "places_total", "trading_country", "dispatch_country",
    "incoterms_place", "transport_ids", "container", "transport_at_border_type",
})

#: Редактируемые поля позиции.
ITEM_FIELDS: frozenset[str] = frozenset({
    "description", "hs_code", "origin_country", "qty", "qty_unit",
    "net_kg", "gross_kg", "packages", "supp_qty", "supp_unit", "price",
})

_ITEM_PATH_RE = re.compile(r"^items\[(\d+)\]\.([a-z_]+)$")


def parse_field_path(path: str, n_items: int) -> tuple[str, int | None, str]:
    """'freight_to_border' → ('header', None, поле); 'items[0].net_kg' → ('item', 0, поле).

    Индекс позиции — с нуля. Невалидный путь → ValueError с причиной.
    """
    if path in HEADER_FIELDS:
        return ("header", None, path)
    match = _ITEM_PATH_RE.match(path)
    if match:
        idx, field = int(match.group(1)), match.group(2)
        if field not in ITEM_FIELDS:
            raise ValueError(f"поле позиции «{field}» не редактируется fixer'ом")
        if idx >= n_items:
            raise ValueError(f"позиция items[{idx}] не существует (всего {n_items})")
        return ("item", idx, field)
    raise ValueError(f"путь «{path}» не входит в whitelist редактируемых полей")


def apply_patch(model: ShipmentIn, path: str, raw_value: str | None) -> ShipmentIn:
    """Применить значение к копии досье. Коэрция типов — на pydantic.

    raw_value None или '' → поле очищается (None).
    ValidationError/ValueError пробрасываются вызывающему.
    """
    scope, idx, field = parse_field_path(path, len(model.items))
    value: object = raw_value if raw_value not in (None, "") else None
    data = model.model_dump(mode="json")
    if scope == "header":
        data[field] = value
    else:
        data["items"][idx][field] = value
    try:
        return ShipmentIn(**data)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ValueError(
            f"значение «{raw_value}» не подходит для {path}: {first.get('msg')}"
        ) from exc


# ── LLM-схема (уровень 95) ───────────────────────────────────────────────────

class FixSuggestion(BaseModel):
    finding_rule: str = Field(description="Код правила из списка findings (например 'F-04')")
    finding_graph: str | None = Field(
        default=None, description="Графа finding'а, если была указана"
    )
    action: Literal["propose_value", "ask_user", "cannot_fix"]
    field_path: str | None = Field(
        default=None,
        description="Для propose_value: 'freight_to_border' или 'items[0].net_kg' (индекс с 0)",
    )
    proposed_value: str | None = Field(
        default=None, description="Новое значение строкой; null/пусто = очистить поле"
    )
    question: str | None = Field(
        default=None, description="Для ask_user: конкретный вопрос человеку"
    )
    why: str = Field(description="Почему это исправление/вопрос; на что опираешься")
    evidence: list[str] = Field(
        default_factory=list, description="Ссылки на данные досье/документов"
    )


class FixerResult(BaseModel):
    analysis: str = Field(
        description="Шаг 1: разбор findings — что общего, что чинится данными досье/документов"
    )
    suggestions: list[FixSuggestion] = Field(default_factory=list)


def _finding_key(rule: str, graph: str | None) -> tuple[str, str | None]:
    return (rule, graph)


def _errors_count(findings: list[Finding]) -> int:
    return sum(1 for f in findings if f.severity == "error")


async def verify_suggestion(
    session: AsyncSession,
    model: ShipmentIn,
    suggestion: FixSuggestion,
    *,
    shipment_ref: str | None,
    doc_payloads: list[dict],
    baseline_findings: list[Finding],
    documents_g44: list | None = None,
) -> dict:
    """Проверить propose_value повторным прогоном сборки+ФЛК (без персиста)."""
    # Ленивый импорт: service импортирует crosscheck/documents, fixer нужен
    # роутеру рядом с service — держим зависимость односторонней в runtime.
    from feap.dt.service import assemble_and_validate

    if suggestion.action != "propose_value":
        return {"applied": False, "verified": None, "note": "не является правкой значения"}
    if not suggestion.field_path:
        return {"applied": False, "verified": False, "note": "не указан field_path"}

    try:
        patched = apply_patch(model, suggestion.field_path, suggestion.proposed_value)
    except ValueError as exc:
        return {"applied": False, "verified": False, "note": str(exc)}

    assembled, findings = await assemble_and_validate(
        session, patched, shipment_ref=shipment_ref, doc_payloads=doc_payloads,
        documents_g44=documents_g44,
    )
    target = _finding_key(suggestion.finding_rule, suggestion.finding_graph)
    remaining = {_finding_key(f.rule, f.graph) for f in findings}
    # Если LLM не указал графу — считаем цель ушедшей, когда исчезли все
    # findings этого правила.
    if suggestion.finding_graph is None:
        target_gone = all(f.rule != suggestion.finding_rule for f in findings)
    else:
        target_gone = target not in remaining

    errors_before = _errors_count(baseline_findings)
    errors_after = _errors_count(findings)
    verified = bool(target_gone and errors_after <= errors_before)
    return {
        "applied": True,
        "verified": verified,
        "target_gone": target_gone,
        "errors_before": errors_before,
        "errors_after": errors_after,
        "status_after": draft_status(findings, assembled.missing_parameters),
        "note": None if verified else (
            "finding не исчез после правки" if not target_gone
            else "правка добавляет новые ошибки"
        ),
    }


async def suggest_fixes(
    session: AsyncSession,
    model: ShipmentIn,
    findings: list[Finding],
    *,
    shipment_ref: str | None = None,
    doc_payloads: list[dict] | None = None,
    documents_g44: list | None = None,
) -> FixOut:
    """LLM-предложения по findings драфта + верификация каждого propose_value."""
    actionable = [f for f in findings if f.severity in ("error", "warning")]
    if not actionable:
        return FixOut(suggestions=[], analysis=None, llm_used=False)

    doc_payloads = doc_payloads or []
    system_prompt = render("system/dt_fixer.jinja2")
    user_message = render(
        "context/dt_fixer_input.jinja2",
        findings=[f.model_dump(mode="json") for f in actionable],
        shipment=model.model_dump(mode="json"),
        docs=[
            {
                "kind": p.get("kind"),
                "label": (p.get("doc") or {}).get("number"),
                "doc": p.get("doc"),
            }
            for p in doc_payloads
        ],
        n_items=len(model.items),
        header_fields=sorted(HEADER_FIELDS),
        item_fields=sorted(ITEM_FIELDS),
    )
    result = await llm_classify(
        level=95,
        system_prompt=system_prompt,
        user_message=user_message,
        output_model=FixerResult,
    )

    known_rules = {f.rule for f in actionable}
    suggestions_out: list[dict] = []
    for suggestion in result.suggestions:
        dump = suggestion.model_dump(mode="json")
        if suggestion.finding_rule not in known_rules:
            dump["verification"] = {
                "applied": False, "verified": False,
                "note": f"правило «{suggestion.finding_rule}» отсутствует в findings драфта",
            }
        else:
            dump["verification"] = await verify_suggestion(
                session, model, suggestion,
                shipment_ref=shipment_ref,
                doc_payloads=doc_payloads,
                baseline_findings=actionable,
                documents_g44=documents_g44,
            )
        suggestions_out.append(dump)

    return FixOut(
        suggestions=suggestions_out,
        analysis=result.analysis,
        llm_used=True,
    )
