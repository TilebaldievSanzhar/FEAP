"""SGR-схемы извлечения из документов: типизация (Routing) + экстракторы (Cascade).

Договорённости LLM-схем:
- Все числа (деньги, веса, количества) — строками с точкой ('1234.56').
  Пересчёт и сравнение делает только код (checks.py) через Decimal —
  структурный вывод с типом number у части провайдеров ломает точность.
- Порядок полей = порядок рассуждений (SGR Cascade): сводка/профиль документа
  идёт первым полем, самопроверка итогов — последним.
- Минимальная кардинальность строк (MinLen) сознательно НЕ кодируется в
  json_schema: часть OpenRouter-провайдеров отвергает minItems в strict-режиме.
  Требование живёт уровнем ниже — в checks.py пустой список строк даёт finding.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DocKind = Literal["invoice", "packing", "cmr", "contract", "cert_origin", "unknown"]

#: Типы, для которых есть экстрактор полей (уровень 91).
EXTRACTABLE_KINDS: tuple[str, ...] = (
    "invoice", "packing", "cmr", "contract", "cert_origin",
)


# ── Типизация документа (Routing, уровень 90) ───────────────────────────────

class DocTypeResult(BaseModel):
    """Выбор типа документа. Cascade: сводка → маркеры → вердикт."""

    content_summary: str = Field(
        description="Шаг 1: 1–3 предложения — что это за текст, какие блоки видны"
    )
    type_markers: list[str] = Field(
        default_factory=list,
        description="Шаг 2: конкретные наблюдаемые признаки типа (заголовки, реквизиты)",
    )
    kind: DocKind = Field(description="Шаг 3: тип документа")
    confidence: float = Field(ge=0, le=1, description="Уверенность 0..1, честно")
    language: str | None = Field(default=None, description="Язык документа, если ясен")


# ── Общие блоки ──────────────────────────────────────────────────────────────

class DocParty(BaseModel):
    """Сторона, как она указана в документе (без нормализации)."""

    name: str | None = Field(default=None, description="Наименование как в документе")
    country: str | None = Field(default=None, description="Страна (ISO-2), если указана")
    address: str | None = None
    inn: str | None = Field(default=None, description="ИНН/УНП/TIN, если есть")


# ── Инвойс ───────────────────────────────────────────────────────────────────

class InvoiceLine(BaseModel):
    line_no: int = Field(description="Номер строки в документе, с 1")
    description: str = Field(description="Наименование товара как в документе")
    sku: str | None = Field(default=None, description="Артикул/модель, если есть")
    qty: str | None = Field(default=None, description="Количество, число строкой")
    qty_unit: str | None = None
    unit_price: str | None = Field(default=None, description="Цена за единицу, число строкой")
    amount: str | None = Field(default=None, description="Сумма строки, число строкой")
    origin_country: str | None = Field(default=None, description="Страна происхождения, если указана")


class InvoiceExtraction(BaseModel):
    document_profile: str = Field(
        description="Шаг 1: сводка документа — стороны, число строк, валюта, итог"
    )
    number: str | None = Field(default=None, description="Номер инвойса")
    doc_date: str | None = Field(default=None, description="Дата, YYYY-MM-DD")
    seller: DocParty | None = None
    buyer: DocParty | None = None
    currency: str | None = Field(default=None, description="Валюта, ISO-3 (USD, EUR...)")
    incoterms: str | None = Field(default=None, description="Базис поставки, если указан (FOB, CIF...)")
    incoterms_place: str | None = None
    payment_terms: str | None = None
    lines: list[InvoiceLine] = Field(default_factory=list, description="Товарные строки")
    total_amount: str | None = Field(default=None, description="Итог инвойса, число строкой")
    totals_selfcheck: str = Field(
        description="Последний шаг: сам сверь сумму строк с итогом и запиши вывод"
    )
    quality_notes: list[str] = Field(
        default_factory=list,
        description="Нечитаемые места OCR, сомнительные цифры",
    )


# ── Упаковочный лист ─────────────────────────────────────────────────────────

class PackingLine(BaseModel):
    line_no: int = Field(description="Номер строки, с 1")
    description: str
    sku: str | None = None
    qty: str | None = Field(default=None, description="Количество, число строкой")
    qty_unit: str | None = None
    packages: int | None = Field(default=None, description="Число мест по строке")
    net_kg: str | None = Field(default=None, description="Вес нетто, кг, число строкой")
    gross_kg: str | None = Field(default=None, description="Вес брутто, кг, число строкой")


class PackingExtraction(BaseModel):
    document_profile: str = Field(description="Шаг 1: сводка документа")
    number: str | None = None
    doc_date: str | None = Field(default=None, description="Дата, YYYY-MM-DD")
    lines: list[PackingLine] = Field(default_factory=list)
    packages_total: int | None = Field(default=None, description="Итого мест")
    net_total_kg: str | None = Field(default=None, description="Итого нетто, кг")
    gross_total_kg: str | None = Field(default=None, description="Итого брутто, кг")
    totals_selfcheck: str = Field(
        description="Последний шаг: сам сверь суммы строк с итогами и запиши вывод"
    )
    quality_notes: list[str] = Field(default_factory=list)


# ── CMR ──────────────────────────────────────────────────────────────────────

class CmrExtraction(BaseModel):
    document_profile: str = Field(description="Шаг 1: сводка документа")
    number: str | None = None
    doc_date: str | None = Field(default=None, description="Дата, YYYY-MM-DD")
    sender: DocParty | None = Field(default=None, description="Графа 1: отправитель")
    receiver: DocParty | None = Field(default=None, description="Графа 2: получатель")
    carrier: DocParty | None = Field(default=None, description="Графа 16: перевозчик")
    loading_place: str | None = Field(default=None, description="Место погрузки")
    unloading_place: str | None = Field(default=None, description="Место разгрузки")
    vehicle_numbers: str | None = Field(default=None, description="Номера тягача/полуприцепа")
    places: int | None = Field(default=None, description="Число мест")
    gross_kg: str | None = Field(default=None, description="Вес брутто, кг, число строкой")
    attached_docs: list[str] = Field(
        default_factory=list, description="Прилагаемые документы (графа 5)"
    )
    quality_notes: list[str] = Field(default_factory=list)


# ── Контракт ─────────────────────────────────────────────────────────────────

class ContractExtraction(BaseModel):
    document_profile: str = Field(description="Шаг 1: сводка документа")
    number: str | None = None
    doc_date: str | None = Field(default=None, description="Дата, YYYY-MM-DD")
    seller: DocParty | None = None
    buyer: DocParty | None = None
    currency: str | None = Field(default=None, description="Валюта контракта, ISO-3")
    incoterms: str | None = None
    incoterms_place: str | None = None
    total_amount: str | None = Field(
        default=None, description="Сумма контракта, если зафиксирована; число строкой"
    )
    payment_terms: str | None = None
    validity: str | None = Field(default=None, description="Срок действия")
    goods_summary: str | None = Field(default=None, description="Предмет контракта, кратко")
    quality_notes: list[str] = Field(default_factory=list)


# ── Сертификат происхождения ─────────────────────────────────────────────────

class CertOriginExtraction(BaseModel):
    document_profile: str = Field(description="Шаг 1: сводка документа")
    number: str | None = None
    doc_date: str | None = Field(default=None, description="Дата, YYYY-MM-DD")
    form: str | None = Field(default=None, description="Форма: СТ-1, A, EUR.1, общая...")
    exporter: DocParty | None = None
    importer: DocParty | None = None
    origin_country: str | None = Field(default=None, description="Страна происхождения")
    origin_criteria: str | None = Field(default=None, description="Критерий происхождения")
    goods_summary: str | None = Field(default=None, description="Описание товаров, кратко")
    hs_codes_mentioned: list[str] = Field(
        default_factory=list, description="Упомянутые коды ТН ВЭД, как в документе"
    )
    quality_notes: list[str] = Field(default_factory=list)


#: kind → схема экстрактора (диспетчеризация кодом, не Union в json_schema:
#: тип уже выбран на уровне 90, а anyOf в strict-режиме поддержан не всеми
#: провайдерами OpenRouter).
EXTRACTION_MODELS: dict[str, type[BaseModel]] = {
    "invoice": InvoiceExtraction,
    "packing": PackingExtraction,
    "cmr": CmrExtraction,
    "contract": ContractExtraction,
    "cert_origin": CertOriginExtraction,
}


# ── Программные проверки (не LLM-схема — сериализация в extracted) ──────────

class DocCheck(BaseModel):
    """Результат пересчёта кодом (checks.py)."""

    check: str = Field(description="Код проверки, например 'D-INV-01'")
    status: Literal["ok", "mismatch", "skipped"]
    message: str
    expected: str | None = None
    actual: str | None = None
