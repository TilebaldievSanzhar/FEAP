"""API request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from feap.schemas.validation import ClassificationResult, PartialResult


class ClassifyRequest(BaseModel):
    description: str = Field(min_length=3, description="Product description")
    additional_info: dict[str, str] = Field(
        default_factory=dict, description="Additional product parameters"
    )


class ClassifyResponse(BaseModel):
    session_id: str
    status: str = Field(description="completed | needs_input | error")
    result: ClassificationResult | None = None
    partial: PartialResult | None = None
    error: str | None = None


class AnswerRequest(BaseModel):
    answers: dict[str, str] = Field(
        description="Key-value answers to clarifying questions, e.g. {'мощность': '1.5 кВт'}"
    )


class TnvedNodeResponse(BaseModel):
    code: str
    code_formatted: str | None
    level: int
    name: str
    duty_rate: str | None
    children_count: int = 0


class VatExemptionInfo(BaseModel):
    """Информация об освобождении от НДС."""

    type: str = Field(description="full | reduced | zero")
    legal_ref: str = Field(description="Ссылка на НПА")
    note: str | None = None
    valid_to: str | None = Field(default=None, description="Дата окончания действия")


class VatConditionalInfo(BaseModel):
    """Условная льгота по НДС: применяется только при целевом назначении."""

    source: str = Field(description="Ключ льготы для ShipmentItemIn.vat_benefit")
    rate: str
    condition: str
    legal_ref: str
    note: str | None = None
    valid_to: str | None = None


class VatInfo(BaseModel):
    """НДС при импорте."""

    rate: str = Field(description="Ставка НДС, e.g. '12%' или '0%'")
    exemption: VatExemptionInfo | None = None
    conditional: list[VatConditionalInfo] = Field(
        default_factory=list,
        description="Льготы, доступные при условии (ставка rate применяется без выбора)",
    )


class TradeRemedyRateInfo(BaseModel):
    rate_pct: str
    country: str | None = None
    producers: list[str] = Field(default_factory=list,
                                 description="Пусто = единая ставка / прочие производители")
    variant: str | None = None


class TradeRemedyInfo(BaseModel):
    """Мера защиты рынка ЕАЭС, совпавшая по коду (может применяться)."""

    measure_no: str = Field(description="Номер в реестре ЕЭК, например AD-32")
    kind: str = Field(description="antidumping | special | countervailing")
    title: str
    product_description: str | None = None
    countries: list[str]
    rates: list[TradeRemedyRateInfo]
    valid_to: str | None = None
    notes: str | None = None
    legal_basis: str | None = None
    source_url: str | None = None
    status: str = Field(
        default="may_apply",
        description="may_apply — совпадение по коду; товар должен подпадать под описание меры",
    )


class TradeRemedyRegistryInfo(BaseModel):
    """Мера защиты рынка ЕАЭС в реестре (справочник /reference/trade-remedies)."""

    measure_no: str = Field(description="Номер в реестре ЕЭК, например AD-32")
    kind: str = Field(description="antidumping | special | countervailing")
    title: str
    product_description: str | None = None
    countries: list[str]
    codes: list[str] = Field(description="Коды ТН ВЭД меры: 10 знаков или префиксы позиций")
    rates: list[TradeRemedyRateInfo]
    valid_from: str | None = None
    valid_to: str | None = None
    notes: str | None = None
    legal_basis: str | None = None
    source_url: str | None = None
    needs_review: bool = Field(
        default=False, description="Ставку из текста реестра не удалось разобрать",
    )
    status: str = Field(description="applied — применяется | suspended — временно не применяется")


class TradeAgreementInfo(BaseModel):
    country: str
    regime: str = Field(description="fta | fta_interim | eaeu_member")
    agreement: str
    status: str = Field(description="in_force | signed | terminated")
    in_force_from: str | None = None
    origin_proof: str | None = None
    note: str | None = None
    verified: bool = False


class ExciseInfo(BaseModel):
    """Информация об акцизе."""

    applicable: bool = Field(description="Является ли товар подакцизным")
    product_name: str | None = Field(default=None, description="Наименование подакцизного товара")
    rate: str | None = Field(default=None, description="Ставка акциза, e.g. '300'")
    rate_unit: str | None = Field(default=None, description="Единица: сом/литр, сом/тонну, сом/1000 шт")
    legal_ref: str | None = Field(default=None, description="Ссылка на НПА")
    note: str | None = None


# ── Non-tariff measures ──────────────────────────────────────────────────────

class NtmMeasureInfo(BaseModel):
    """Одна мера нетарифного регулирования."""

    measure_type: str = Field(description="Тип меры: Технический регламент, Лицензирование и т.д.")
    measure_type_kind: int = Field(description="Kind из TWS.BY R12 (1-13)")
    name: str = Field(description="Название меры")
    status: str = Field(
        default="may_apply",
        description="applies | may_apply | informational",
    )
    qualifier: str | None = Field(
        default=None,
        description="Квалификатор из НПА — какие именно товары подпадают",
    )
    legal_basis: str | None = None
    note: str | None = None
    scope: str = Field(default="eaeu", description="eaeu | national")
    valid_to: str | None = None


class NtmSummary(BaseModel):
    """Сводка по нетарифным мерам для кода ТН ВЭД."""

    measures: list[NtmMeasureInfo] = Field(default_factory=list)
    total_count: int = 0
    has_prohibitions: bool = False
    has_licensing: bool = False
    has_technical_regulations: bool = False
    has_sanitary_control: bool = False
    has_veterinary_control: bool = False
    has_phytosanitary_control: bool = False
    has_marking: bool = False
    data_source: str | None = None
    data_date: str | None = None
    note: str | None = Field(
        default=None,
        description="Предупреждение о двойном соответствии код+описание",
    )


class NtmResponse(BaseModel):
    """Ответ эндпоинта GET /ntm/{code}."""

    code: str
    code_formatted: str | None = None
    name: str | None = None
    ntm: NtmSummary


class CodeLookupResponse(BaseModel):
    """Справка по 10-значному коду ТН ВЭД."""

    code: str
    code_formatted: str
    name: str
    duty_rate: str | None = None
    vat: VatInfo
    excise: ExciseInfo
    unit_primary: str | None = None
    unit_secondary: str | None = None
    ntm: NtmSummary | None = None
    trade_remedies: list[TradeRemedyInfo] = Field(
        default_factory=list,
        description="Действующие меры защиты рынка ЕАЭС по коду (антидемпинг и др.)",
    )
    customs_fee: str | None = Field(
        default=None,
        description="Правило сбора за таможенные операции КР (п. 38 ПП КР № 79)",
    )
