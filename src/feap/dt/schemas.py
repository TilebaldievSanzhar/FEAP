"""Pydantic-модели ДТ-ядра: досье, позиции, графы, платежи, findings.

Деньги и веса — Decimal (сериализуются строками, чтобы JSONB не терял точность).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

Incoterms = Literal[
    "EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP"
]
AllocationMethod = Literal["gross_weight", "value"]
Severity = Literal["error", "warning", "info"]
DraftStatus = Literal["ready", "has_errors", "needs_input"]

_MoneyStr = Annotated[Decimal, Field(ge=0)]

# Pydantic v2 сериализует Decimal строкой в mode="json" — графы в JSONB
# сохраняют точность без кастомных энкодеров.
_DecimalAsStr = BaseModel


#: Графа 36: значения «без льгот» (нули / буква О кириллицей и латиницей).
NO_PREFERENCE = frozenset({"0000", "ОООО", "OOOO"})


# ── Входные модели досье ────────────────────────────────────────────────────

class RemedyDecision(_DecimalAsStr):
    """Решение декларанта по мере защиты рынка, совпавшей по коду и стране.

    Код определяет только «может применяться»: подпадает ли товар под
    описание меры (диаметр, назначение и т.п.), решает человек.
    """

    decision: Literal["applies", "not_applicable", "exempt"] = Field(
        description="applies — пошлина уплачивается; not_applicable — товар не подпадает "
                    "под описание меры; exempt — освобождение (квота, целевое назначение, "
                    "ценовые обязательства) с подтверждающим документом в графе 44",
    )
    variant: str | None = Field(
        None, description="Вид товара, если ставки меры различаются по видам (AD-1 и т.п.)",
    )
    rate_pct: Decimal | None = Field(
        None, ge=0, le=500,
        description="Ставка вручную, % — только если ставку меры не удалось разобрать",
    )
    note: str | None = None


class PartyIn(_DecimalAsStr):
    name: str = Field(min_length=1)
    country: str | None = Field(None, pattern=r"^[A-Z]{2}$")
    address: str | None = None
    inn: str | None = None


class ShipmentItemIn(_DecimalAsStr):
    description: str = Field(min_length=1, description="Описание товара (основа графы 31)")
    hs_code: str | None = Field(None, pattern=r"^\d{10}$", description="Графа 33")
    origin_country: str | None = Field(None, description="Графа 34 (2 буквы или 'EU')")
    qty: Decimal | None = Field(None, ge=0, description="Коммерческое количество")
    qty_unit: str | None = None
    price: _MoneyStr = Field(description="Графа 42: цена позиции в валюте контракта")
    net_kg: Decimal | None = Field(None, ge=0, description="Графа 38")
    gross_kg: Decimal | None = Field(None, ge=0, description="Графа 35")
    packages: int | None = Field(None, ge=0, description="Число мест позиции")
    supp_qty: Decimal | None = Field(None, ge=0, description="Графа 41: доп. единица")
    supp_unit: str | None = None
    preference: str | None = Field(
        None, max_length=4,
        description="Графа 36: код преференции (классификатор); None = без льгот",
    )
    preferential_duty_rate: str | None = Field(
        None, max_length=200,
        description="Ставка ввозной пошлины по графику ЗСТ (в формате ЕТТ: '0%', '5%'). "
                    "Применяется только при действующем соглашении со страной "
                    "происхождения и коде преференции в графе 36",
    )
    manufacturer: str | None = Field(
        None, max_length=300,
        description="Производитель товара (графа 31); нужен для индивидуальных "
                    "ставок антидемпинговых мер",
    )
    remedy_decisions: dict[str, RemedyDecision] = Field(
        default_factory=dict,
        description="Решения по мерам защиты рынка: {'AD-32': {'decision': 'applies'}}",
    )
    vat_benefit: str | None = Field(
        None, max_length=40,
        description="Условная льгота по НДС, выбранная декларантом (source льготы, "
                    "например 'pkm816_a2'); без выбора применяется общая ставка",
    )
    sku: str | None = Field(None, description="Артикул — связь с каталогом компании")
    classification_ref: str | None = Field(
        None, description="Ссылка на session/job item классификации (аудит)"
    )
    extra: dict[str, Any] = Field(default_factory=dict)


class ShipmentIn(_DecimalAsStr):
    procedure: Literal["IM40"] = "IM40"
    seller: PartyIn
    buyer: PartyIn
    declarant: PartyIn | None = Field(None, description="Графа 14; по умолчанию = buyer")
    trading_country: str | None = Field(None, pattern=r"^[A-Z]{2}$", description="Графа 11")
    dispatch_country: str | None = Field(None, pattern=r"^[A-Z]{2}$", description="Графа 15")
    destination_country: str = Field("KG", pattern=r"^[A-Z]{2}$", description="Графа 17")
    incoterms: Incoterms = Field(description="Графа 20")
    incoterms_place: str | None = None
    deal_nature: str | None = Field(
        None, max_length=3,
        description="Графа 24: код характера сделки (классификатор Решения 378)",
    )
    contract_currency: str = Field(pattern=r"^[A-Z]{3}$", description="Графа 22")

    # Доначисления/вычеты к таможенной стоимости — в валюте контракта.
    freight_to_border: _MoneyStr = Decimal(0)
    freight_beyond_border: _MoneyStr = Field(
        Decimal(0), description="Вычет: фрахт после границы ЕАЭС (если выделен документально)"
    )
    insurance: _MoneyStr = Decimal(0)
    other_charges: _MoneyStr = Field(Decimal(0), description="Погрузка/лицензионные и пр. до границы")
    allocation_method: AllocationMethod = "gross_weight"

    rate_date: dt.date | None = Field(None, description="Дата курса НБКР (графа 23); None = сегодня")

    transport_at_border_type: str | None = Field(None, description="Графа 25: код вида транспорта")
    transport_ids: str | None = Field(None, description="Графы 18/21: номера ТС")
    container: bool | None = Field(None, description="Графа 19")
    places_total: int | None = Field(None, ge=0, description="Графа 6")

    items: list[ShipmentItemIn] = Field(default_factory=list, max_length=999)

    @field_validator("items")
    @classmethod
    def _max_999(cls, v: list[ShipmentItemIn]) -> list[ShipmentItemIn]:
        if len(v) > 999:
            raise ValueError("В одной ДТ не может быть больше 999 товаров")
        return v


# ── Платежи (графа 47) ──────────────────────────────────────────────────────

PaymentKind = Literal[
    "fee", "duty", "special", "antidumping", "countervailing", "excise", "vat",
]

#: Виды «пошлин защиты рынка» (Протокол, прил. 8 к Договору о ЕАЭС).
REMEDY_KINDS: frozenset[str] = frozenset({"special", "antidumping", "countervailing"})

#: Коды видов платежей для графы 47 (Прил. 9 к Решению КТС № 378).
#: 1010/2010/2040/2050/2060/5010 — раздел «на всей таможенной территории ЕАЭС»,
#: сверено 2026-10-13. 4010 (акциз) — национальный раздел, TODO(verify) по ДТ КР.
PAYMENT_CODES: dict[str, str] = {
    "fee": "1010",
    "duty": "2010",
    "special": "2040",
    "antidumping": "2050",
    "countervailing": "2060",
    "excise": "4010",
    "vat": "5010",
}


class PaymentLine(_DecimalAsStr):
    kind: PaymentKind
    code: str = Field(description="Код вида платежа (Прил. 9 к Решению 378)")
    basis: Decimal | None = Field(None, description="Основа начисления")
    rate: str = Field(description="Ставка в человекочитаемом виде")
    amount: Decimal | None = Field(None, description="Сумма, сом; None если не рассчитано")
    missing: list[str] = Field(default_factory=list, description="Чего не хватило для расчёта")
    note: str | None = None
    ref: str | None = Field(None, description="Мера/льгота, на которой основана строка (AD-32)")


# ── Findings (ФЛК) ──────────────────────────────────────────────────────────

class Finding(_DecimalAsStr):
    rule: str = Field(description="Код правила, например 'F-04'")
    severity: Severity
    graph: str | None = Field(None, description="Ссылка на графу: 'g22' или 'items[2].g38'")
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


# ── Результат сборки ────────────────────────────────────────────────────────

class AssembledDraft(_DecimalAsStr):
    graphs: dict[str, Any]
    missing_parameters: list[str] = Field(default_factory=list)


class DtDraftOut(_DecimalAsStr):
    id: uuid.UUID
    shipment_id: uuid.UUID
    version: int
    status: DraftStatus
    structure_version: str
    graphs: dict[str, Any]
    findings: list[Finding]
    missing_parameters: list[str]
    created_at: dt.datetime


class ShipmentOut(_DecimalAsStr):
    id: uuid.UUID
    org_id: uuid.UUID | None
    status: str
    data: ShipmentIn
    created_at: dt.datetime
    updated_at: dt.datetime
    latest_draft_id: uuid.UUID | None = None
    latest_draft_version: int | None = None


class ShipmentListEntry(_DecimalAsStr):
    id: uuid.UUID
    status: str
    procedure: str
    buyer_name: str | None = None
    items_count: int
    items_without_code: int
    created_at: dt.datetime
    updated_at: dt.datetime


class ShipmentListOut(_DecimalAsStr):
    items: list[ShipmentListEntry]
    total: int


# ── Классификация позиций досье (связка с каскадом) ─────────────────────────

class ShipmentClassifyOut(_DecimalAsStr):
    """Ответ на запуск классификации: ссылка на стандартный batch-job."""

    job_id: uuid.UUID
    status: str = "pending"
    enqueued: int = Field(description="Сколько позиций отправлено в классификацию")
    skipped: int = Field(description="Сколько позиций пропущено (код уже есть)")
    poll_url: str
    items_url: str
    events_url: str


# ── Каталог товаров компании ────────────────────────────────────────────────

class CatalogItemIn(_DecimalAsStr):
    sku: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1)
    hs_code: str | None = Field(None, pattern=r"^\d{10}$")
    attributes: dict[str, Any] = Field(default_factory=dict)
    approved_by: str | None = Field(None, description="Кто утвердил код (аудит)")
    passport: dict[str, Any] | None = Field(
        None,
        description="Паспорт товара (calc.schemas.PassportIn: calc + classification); "
                    "None — не менять сохранённый",
    )


class CatalogItemOut(_DecimalAsStr):
    id: uuid.UUID
    org_id: uuid.UUID
    sku: str
    description: str
    hs_code: str | None
    attributes: dict[str, Any]
    passport: dict[str, Any] = Field(default_factory=dict)
    usage_count: int
    approved_by: str | None
    approved_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime


class CatalogListOut(_DecimalAsStr):
    items: list[CatalogItemOut]
    total: int


class CatalogImportOut(_DecimalAsStr):
    """Итог переноса кодов из досье в каталог."""

    imported: int = Field(description="Создано новых записей")
    updated: int = Field(description="Обновлено существующих")
    skipped: int = Field(description="Пропущено (нет SKU или кода)")


# ── Документы досье (Фаза 2, SGR-слой) ──────────────────────────────────────

DocumentKind = Literal[
    "auto", "invoice", "packing", "cmr", "awb", "rail",
    "contract", "cert_origin", "permit", "other",
]


class DocumentIn(_DecimalAsStr):
    """Привязка документа к досье. Источник текста — ровно один:
    прямой text ЛИБО ссылка на файл завершённого OCR-job'а."""

    kind: DocumentKind = Field(
        "auto", description="Тип документа; auto = определит LLM при извлечении"
    )
    number: str | None = None
    doc_date: dt.date | None = None
    text: str | None = Field(None, min_length=1, description="Прямой текст документа")
    ocr_job_id: uuid.UUID | None = Field(
        None, description="Взять текст из завершённого OCR-job'а"
    )
    file_index: int = Field(0, ge=0, description="Индекс файла внутри OCR-job'а")

    @field_validator("ocr_job_id")
    @classmethod
    def _one_source(cls, v: uuid.UUID | None, info: Any) -> uuid.UUID | None:
        text = info.data.get("text")
        if (text is None) == (v is None):
            raise ValueError("Укажите ровно один источник: text ИЛИ ocr_job_id")
        return v


class DocumentOut(_DecimalAsStr):
    id: uuid.UUID
    shipment_id: uuid.UUID
    kind: str
    number: str | None
    doc_date: dt.date | None
    source_origin: str | None = Field(None, description="'text' | 'ocr'")
    ocr_job_id: uuid.UUID | None = None
    text_chars: int = 0
    status: str = Field("attached", description="attached | extracted | needs_kind")
    doc_type: dict[str, Any] | None = Field(None, description="Результат типизации (LLM)")
    doc: dict[str, Any] | None = Field(None, description="Извлечённые поля (LLM)")
    checks: list[dict[str, Any]] = Field(
        default_factory=list, description="Пересчёт итогов кодом (D-xx)"
    )
    sensor_warnings: list[str] = Field(default_factory=list)
    extracted_at: str | None = None
    created_at: dt.datetime


class DocumentListOut(_DecimalAsStr):
    items: list[DocumentOut]
    total: int


class PermitCheckOut(_DecimalAsStr):
    """Результат проверки разрешительного (формат + реестры TULPAR)."""

    document_id: uuid.UUID
    status: str = Field(
        description="format_invalid | format_ok | found | not_found | "
                    "status_mismatch | code_mismatch | unavailable"
    )
    detail: str
    source: str = Field(description="'format' | 'registry'")
    checked_at: str
    cached: bool = False


# ── Кросс-сверка (Фаза 2) ────────────────────────────────────────────────────

class CrosscheckOut(_DecimalAsStr):
    """Числа сверяет код (X-xx), сущности нечётко сопоставляет LLM."""

    findings: list[Finding] = Field(description="Числовые сверки X-xx (код)")
    parties: list[dict[str, Any]] = Field(
        default_factory=list, description="Сопоставление сторон (LLM, match_confidence)"
    )
    invoice_lines: list[dict[str, Any]] = Field(
        default_factory=list, description="Строки инвойса ↔ позиции досье (LLM)"
    )
    dropped: list[str] = Field(
        default_factory=list, description="Ответы LLM, отброшенные harness-контролем"
    )
    llm_used: bool = False


# ── Графа 31 (Фаза 2) ────────────────────────────────────────────────────────

class G31Out(_DecimalAsStr):
    """Предложение текста графы 31. Применяет человек (PUT досье)."""

    accepted: bool = Field(description="Прошёл ли текст grounding-валидатор")
    text_ru: str | None = None
    facts: list[dict[str, Any]] = Field(
        default_factory=list, description="Факты, выданные генератору (id/label/value/source)"
    )
    facts_used: list[str] = Field(default_factory=list)
    missing_attributes: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    attempts: int = 1


# ── Риск-советник (Фаза 2) ───────────────────────────────────────────────────

class AdviseOut(_DecimalAsStr):
    """Сигналы считает код; LLM формулирует и прикладывает evidence."""

    signals: list[dict[str, Any]] = Field(default_factory=list)
    advices: list[dict[str, Any]] = Field(default_factory=list)
    code_checks: list[dict[str, Any]] = Field(
        default_factory=list, description="Соответствие описания (31) коду (33)"
    )
    dropped: list[str] = Field(default_factory=list)
    llm_used: bool = False


# ── Findings-fixer (Фаза 2) ──────────────────────────────────────────────────

class FixOut(_DecimalAsStr):
    """Предложения исправлений. Каждое propose_value проверено повторным
    прогоном сборки+ФЛК (verification.verified). Применяет человек."""

    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    analysis: str | None = None
    llm_used: bool = False
