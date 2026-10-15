"""Схемы расчёта по товару без досье и паспорта товара.

Деньги — Decimal (в JSON строками). Платежи считает тот же сборщик, что и
проект ДТ (calc.engine), поэтому PaymentLine и Finding — те же модели.
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from feap.dt.schemas import DraftStatus, Finding, Incoterms, PaymentLine, RemedyDecision

_Money = Annotated[Decimal, Field(ge=0)]

CostKind = Literal["logistics", "broker", "warehouse", "certification", "bank", "other"]

#: Подписи расходов вне таможенной стоимости (если label не задан).
COST_LABELS: dict[str, str] = {
    "logistics": "Доставка после границы",
    "broker": "Услуги таможенного представителя",
    "warehouse": "Склад временного хранения",
    "certification": "Сертификация и разрешительные документы",
    "bank": "Банк и валютный контроль",
    "other": "Прочие расходы",
}


class CostLine(BaseModel):
    """Расход вне таможенной стоимости — входит в себестоимость, но не в платежи."""

    kind: CostKind = "other"
    label: str | None = Field(None, max_length=120, description="Подпись; по умолчанию по kind")
    amount: _Money
    currency: str = Field("KGS", pattern=r"^[A-Z]{3}$", description="KGS или валюта с курсом НБКР")


class CalcParams(BaseModel):
    """Параметры партии. Паспорт товара хранит их между расчётами."""

    origin_country: str | None = Field(
        None, description="Страна происхождения (ISO alpha-2 или EU) — меры защиты рынка, ЗСТ",
    )
    manufacturer: str | None = Field(
        None, max_length=300, description="Производитель — индивидуальные ставки антидемпинга",
    )
    price: _Money | None = Field(None, description="Стоимость партии по счёту, в валюте currency")
    unit_price: _Money | None = Field(
        None, description="Цена за единицу; без price стоимость партии = unit_price × qty",
    )
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    incoterms: Incoterms = Field("FOB", description="Базис поставки — что уже входит в цену")
    freight_to_border: _Money = Field(
        Decimal(0), description="Фрахт до границы ЕАЭС, если не входит в цену (в currency)",
    )
    insurance: _Money = Decimal(0)
    other_charges: _Money = Field(Decimal(0), description="Погрузка и пр. до границы (в currency)")
    freight_beyond_border: _Money = Field(
        Decimal(0), description="Фрахт после границы внутри цены — вычитается из ТС",
    )
    qty: Decimal | None = Field(None, gt=0, description="Количество в партии — для цены за единицу")
    qty_unit: str | None = Field(None, max_length=20)
    net_kg: _Money | None = None
    gross_kg: _Money | None = None
    supp_qty: _Money | None = Field(None, description="Доп. единица (графа 41) — для специфических ставок")
    supp_unit: str | None = Field(None, max_length=20)
    preference: str | None = Field(None, max_length=4, description="Код преференции (графа 36)")
    preferential_duty_rate: str | None = Field(None, max_length=200)
    remedy_decisions: dict[str, RemedyDecision] = Field(default_factory=dict)
    vat_benefit: str | None = Field(None, max_length=40)
    costs: list[CostLine] = Field(default_factory=list, max_length=20)

    @field_validator("origin_country")
    @classmethod
    def _origin(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().upper()
        if len(v) != 2 or not v.isalpha():
            raise ValueError("Страна — две латинские буквы (ISO alpha-2) или EU")
        return v


class CalcIn(CalcParams):
    """Запрос расчёта: код + параметры партии."""

    hs_code: str = Field(pattern=r"^\d{10}$")
    description: str | None = Field(None, max_length=2000, description="Описание товара (для паспорта)")
    rate_date: dt.date | None = Field(None, description="Дата курсов и ставок; по умолчанию сегодня")

    @model_validator(mode="after")
    def _lot_price(self) -> CalcIn:
        """Стоимость партии считает бэкенд: price или unit_price × qty (не клиент)."""
        if self.price is None and self.unit_price is not None and self.qty is not None:
            self.price = (self.unit_price * self.qty).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
        if self.price is None or self.price <= 0:
            raise ValueError("Укажите стоимость партии (price) или цену за единицу и количество")
        return self


class LandedCostLine(BaseModel):
    kind: str = Field(description="goods | additions | duty | antidumping | … | vat | fee | расходы")
    label: str
    amount: Decimal = Field(description="Сумма, сом")
    source_amount: Decimal | None = Field(None, description="Сумма в исходной валюте")
    source_currency: str | None = None
    customs_payment: bool = Field(False, description="Таможенный платёж (графа 47 / сбор)")


class LandedCost(BaseModel):
    """Себестоимость ввоза партии, сом."""

    lines: list[LandedCostLine]
    total: Decimal = Field(description="Всего, включая импортный НДС")
    vat: Decimal = Field(description="Импортный НДС — для плательщика НДС принимается к зачёту")
    total_without_vat: Decimal
    qty: Decimal | None = None
    qty_unit: str | None = None
    per_unit: Decimal | None = None
    per_unit_without_vat: Decimal | None = None


class CalcOut(BaseModel):
    status: DraftStatus
    hs_code: str
    rate_date: dt.date
    currency: str
    fx_rate: Decimal | None = Field(None, description="Курс НБКР валюты счёта на rate_date")
    customs_value: Decimal | None = Field(None, description="Таможенная стоимость (графа 45), сом")
    payments: list[PaymentLine] = Field(
        description="Платежи графы 47 и сбор за таможенные операции (одна ДТ на партию)",
    )
    payments_total: Decimal | None = Field(
        None, description="Сумма платежей; None, если какой-то платёж не рассчитан",
    )
    vat_base_includes_remedies: bool
    tariff: dict[str, Any] = Field(description="След тарифных проверок (как items[i].tariff проекта ДТ)")
    landed_cost: LandedCost | None = Field(None, description="None, пока не хватает данных")
    findings: list[Finding]
    missing_parameters: list[str]


# ── Паспорт товара ───────────────────────────────────────────────────────────

class ClassificationStep(BaseModel):
    level: str = Field(max_length=20, description="section | group | position | subposition | code_10")
    code: str = Field(max_length=20)
    name: str | None = Field(None, max_length=1000)
    reasoning: str | None = Field(None, max_length=4000)


class ClassificationNote(BaseModel):
    """Обоснование кода: цепочка каскада или пометка, что код задан вручную."""

    source: Literal["cascade", "manual", "catalog"] = "manual"
    confidence: float | None = Field(None, ge=0, le=1)
    steps: list[ClassificationStep] = Field(default_factory=list, max_length=8)


class PassportIn(BaseModel):
    """Паспорт в записи «Товары»: параметры партии и обоснование кода."""

    calc: CalcParams | None = None
    classification: ClassificationNote | None = None


class PassportPdfIn(BaseModel):
    """PDF паспорта прямо из расчёта (без сохранения в «Товары»)."""

    calc: CalcIn
    title: str | None = Field(None, max_length=300, description="Наименование товара в шапке")
    sku: str | None = Field(None, max_length=100)
    classification: ClassificationNote | None = None


# ── Карточка товара по фото / скриншоту (LLM уровень 97) ─────────────────────

class ImageSpec(BaseModel):
    name: str = Field(description="Характеристика: материал, мощность, размер…")
    value: str


class ImagePrice(BaseModel):
    """Цена, как она видна на изображении. Не расчёт: пользователь подтверждает."""

    amount: str | None = Field(None, description="Число с точкой, как на изображении: '12.50'")
    currency: str | None = Field(None, description="ISO 4217: CNY, USD, RUB…")
    per: str | None = Field(None, description="За что цена: шт, комплект, кг, м")
    min_order_qty: str | None = Field(None, description="Минимальная партия, если указана")


class ImageProductCard(BaseModel):
    """Что модель увидела на фото или скриншоте карточки товара."""

    is_product: bool = Field(description="На изображении есть конкретный товар")
    not_product_reason: str | None = Field(
        None, description="Если товара нет: что на изображении (счёт, документ, пустой экран…)",
    )
    product_identification: str = Field(description="Что это за товар: тип, категория")
    description_ru: str = Field(
        description="Описание для классификации ТН ВЭД на русском: что это, материал/состав, "
                    "назначение, устройство, комплектность — без цены и рекламы",
    )
    title_original: str | None = Field(None, description="Заголовок товара как на изображении")
    material: str | None = None
    function: str | None = None
    intended_use: str | None = None
    brand: str | None = None
    model: str | None = None
    manufacturer: str | None = Field(None, description="Фабрика/компания-продавец, если видна")
    origin_country: str | None = Field(None, description="ISO alpha-2 — только при явных признаках")
    origin_evidence: str | None = Field(None, description="Почему такая страна происхождения")
    price: ImagePrice | None = None
    specs: list[ImageSpec] = Field(default_factory=list, max_length=15)
    missing_info: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)


class ImagePrefill(BaseModel):
    """Подсказки для формы расчёта — распознаны с изображения, требуют проверки."""

    origin_country: str | None = None
    manufacturer: str | None = None
    unit_price: Decimal | None = Field(None, description="Цена за единицу, как на изображении")
    currency: str | None = None
    qty_unit: str | None = None
    min_order_qty: str | None = None


class ImageCardOut(BaseModel):
    card: ImageProductCard
    classify_description: str = Field(
        description="Готовое описание для POST /classify: описание + ключевые характеристики",
    )
    prefill: ImagePrefill
    images: int = Field(description="Сколько изображений разобрано")
