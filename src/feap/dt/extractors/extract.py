"""Оркестрация извлечения: типизация (90) → экстракция по типу (91) → checks.

Harness-контроли (dt_core_dev_plan.md §4):
- confidence типизации ниже порога → kind='unknown', тип выбирает человек;
- экстрактор выбирается кодом по kind (диспетчеризация, не Union в схеме);
- итоги пересчитываются кодом (checks.py), расхождение → mismatch;
- sensor-паттерны сканируют текст до LLM, срабатывание — флаг, не блокировка.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from feap.config import settings
from feap.dt.extractors.checks import run_checks
from feap.dt.extractors.schemas import (
    EXTRACTABLE_KINDS,
    EXTRACTION_MODELS,
    DocTypeResult,
)
from feap.dt.extractors.sensors import scan_text
from feap.llm.client import classify as llm_classify
from feap.prompts.registry import render

logger = logging.getLogger(__name__)

#: Человекочитаемые ярлыки типов (для промптов).
KIND_LABELS: dict[str, str] = {
    "invoice": "инвойс (счёт-фактура)",
    "packing": "упаковочный лист",
    "cmr": "международная товарно-транспортная накладная (CMR)",
    "contract": "внешнеторговый контракт",
    "cert_origin": "сертификат происхождения",
}

#: Лимит текста, подаваемого в LLM (символы). Контракты бывают на сотни
#: страниц; реквизиты и предмет — в начале и в конце.
MAX_DOC_CHARS = 60_000


def clip_text(text: str, limit: int = MAX_DOC_CHARS) -> str:
    """Обрезать длинный документ: начало + конец (реквизиты живут по краям)."""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return (
        text[:head]
        + f"\n\n[... пропущено {len(text) - limit} символов ...]\n\n"
        + text[-tail:]
    )


async def detect_doc_type(text: str) -> DocTypeResult:
    """Уровень 90: определить тип документа (SGR Routing)."""
    system_prompt = render("system/dt_doc_type.jinja2")
    user_message = render("context/dt_document.jinja2", text=clip_text(text))
    return await llm_classify(
        level=90,
        system_prompt=system_prompt,
        user_message=user_message,
        output_model=DocTypeResult,
    )


async def extract_fields(text: str, kind: str) -> Any:
    """Уровень 91: извлечь поля по типу документа (SGR Cascade)."""
    output_model = EXTRACTION_MODELS[kind]
    system_prompt = render(
        "system/dt_extract.jinja2", kind=kind, kind_label=KIND_LABELS[kind]
    )
    user_message = render("context/dt_document.jinja2", text=clip_text(text))
    return await llm_classify(
        level=91,
        system_prompt=system_prompt,
        user_message=user_message,
        output_model=output_model,
    )


async def extract_document(text: str, *, kind: str | None = None) -> dict:
    """Полный проход: сенсоры → (типизация) → экстракция → checks.

    kind: None/'other' → тип определяет LLM; явный тип (человек выбрал
    или колонка kind уже заполнена) типизацию пропускает.

    Возвращает payload для shipment_documents.extracted (без 'source' —
    его добавляет documents.py):
      {status, kind, doc_type?, doc?, checks, sensor_warnings, extracted_at}
    status: 'extracted' | 'needs_kind' (тип не определён/нет экстрактора).
    """
    sensor_warnings = scan_text(text)

    doc_type_dump: dict | None = None
    effective_kind = kind if kind and kind not in ("other", "unknown", "auto") else None

    if effective_kind is None:
        doc_type = await detect_doc_type(text)
        doc_type_dump = doc_type.model_dump(mode="json")
        effective_kind = doc_type.kind
        # Harness: неуверенный вердикт не принимается — тип выберет человек.
        if (
            effective_kind != "unknown"
            and doc_type.confidence < settings.DT_DOCTYPE_CONFIDENCE_MIN
        ):
            logger.info(
                "doc typing below threshold: kind=%s conf=%.2f < %.2f → unknown",
                effective_kind, doc_type.confidence, settings.DT_DOCTYPE_CONFIDENCE_MIN,
            )
            effective_kind = "unknown"

    now = dt.datetime.now(dt.timezone.utc).isoformat()

    if effective_kind not in EXTRACTABLE_KINDS:
        return {
            "status": "needs_kind",
            "kind": effective_kind,
            "doc_type": doc_type_dump,
            "doc": None,
            "checks": [],
            "sensor_warnings": sensor_warnings,
            "extracted_at": now,
        }

    extraction = await extract_fields(text, effective_kind)
    checks = run_checks(effective_kind, extraction)

    return {
        "status": "extracted",
        "kind": effective_kind,
        "doc_type": doc_type_dump,
        "doc": extraction.model_dump(mode="json"),
        "checks": [c.model_dump(mode="json") for c in checks],
        "sensor_warnings": sensor_warnings,
        "extracted_at": now,
    }
