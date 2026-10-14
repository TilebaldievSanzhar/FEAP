"""Справка по коду ТН ВЭД: тариф, НДС, акциз, меры защиты рынка, НТР, сбор.

Одна функция на `/tnved/lookup/{code}` и паспорт товара — ставки и меры в
API и в PDF не расходятся. Только справочные данные на дату: суммы считает
calc.engine через сборщик проекта ДТ.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from feap.config import settings
from feap.db.models import TnvedNode
from feap.db.queries import (
    get_excise_rate,
    get_ntm_measures_for_code,
    get_trade_remedies,
    get_vat_rules,
)
from feap.schemas.api import (
    CodeLookupResponse,
    ExciseInfo,
    NtmMeasureInfo,
    NtmSummary,
    TradeRemedyInfo,
    TradeRemedyRateInfo,
    VatConditionalInfo,
    VatExemptionInfo,
    VatInfo,
)
from feap.tariff.fees import kg_release_fee_rule
from feap.tariff.remedies import RemedyMeasure, find_code_measures, origin_matches

_CATEGORY_FLAGS = {
    "prohibition": "has_prohibitions",
    "licensing": "has_licensing",
    "technical_regulation": "has_technical_regulations",
    "certification": "has_technical_regulations",
    "sanitary": "has_sanitary_control",
    "veterinary": "has_veterinary_control",
    "phytosanitary": "has_phytosanitary_control",
    "marking": "has_marking",
}


async def build_ntm_summary(db: AsyncSession, code: str) -> NtmSummary:
    """Сводка нетарифных мер по 10-значному коду."""
    rows = await get_ntm_measures_for_code(db, code)

    measures: list[NtmMeasureInfo] = []
    flags: dict[str, bool] = {}

    for measure, measure_code, measure_type in rows:
        info = NtmMeasureInfo(
            measure_type=measure_type.name_ru,
            measure_type_kind=measure_type.kind,
            name=measure.name,
            status="may_apply",
            qualifier=measure_code.product_description,
            legal_basis=measure.legal_basis,
            note=measure.note,
            scope=measure.scope,
            valid_to=measure.valid_to.isoformat() if measure.valid_to else None,
        )
        measures.append(info)

        flag_attr = _CATEGORY_FLAGS.get(measure_type.category)
        if flag_attr:
            flags[flag_attr] = True

    return NtmSummary(
        measures=measures,
        total_count=len(measures),
        has_prohibitions=flags.get("has_prohibitions", False),
        has_licensing=flags.get("has_licensing", False),
        has_technical_regulations=flags.get("has_technical_regulations", False),
        has_sanitary_control=flags.get("has_sanitary_control", False),
        has_veterinary_control=flags.get("has_veterinary_control", False),
        has_phytosanitary_control=flags.get("has_phytosanitary_control", False),
        has_marking=flags.get("has_marking", False),
        note=(
            "Применимость мер зависит не только от кода ТН ВЭД, но и от описания товара. "
            "Проверьте квалификаторы (qualifier) каждой меры."
        ) if measures else None,
    )


def rate_infos(m: RemedyMeasure) -> list[TradeRemedyRateInfo]:
    return [
        TradeRemedyRateInfo(
            rate_pct=format(r.rate_pct, "f"), country=r.country,
            producers=list(r.producers), variant=r.variant,
        )
        for r in m.rates
    ]


def fee_rule_text() -> str | None:
    """Правило сбора за таможенные операции (п. 38 ПП КР № 79) или None, если выключен."""
    if settings.DT_CUSTOMS_FEE_MODE != "percent":
        return None
    try:
        rp = Decimal((settings.DT_CALC_INDEX_KGS or "100").strip())
    except InvalidOperation:
        rp = Decimal(100)
    rule = kg_release_fee_rule(rp)
    return (f"{rule.pct}% от таможенной стоимости ДТ, не менее {rule.min_kgs} сом и "
            f"не более {rule.max_kgs} сом — {rule.legal_ref}")


async def build_code_lookup(
    db: AsyncSession,
    code: str,
    *,
    origin: str | None = None,
    on_date: dt.date | None = None,
) -> CodeLookupResponse | None:
    """Справка по листовому коду на дату; None — кода нет в номенклатуре."""
    day = on_date or dt.date.today()

    result = await db.execute(
        select(TnvedNode).where(TnvedNode.code == code, TnvedNode.is_leaf.is_(True)).limit(1)
    )
    node = result.scalar_one_or_none()
    if not node:
        return None

    # НДС: безусловная льгота применяется сама, условные — по выбору декларанта.
    exemption, conditional = await get_vat_rules(db, code, day)
    if exemption:
        vat = VatInfo(
            rate=exemption.rate_override or "0%",
            exemption=VatExemptionInfo(
                type=exemption.exemption_type,
                legal_ref=exemption.legal_ref,
                note=exemption.note,
                valid_to=exemption.valid_to.isoformat() if exemption.valid_to else None,
            ),
        )
    else:
        vat = VatInfo(rate=settings.VAT_RATE_DEFAULT)
    vat.conditional = [
        VatConditionalInfo(
            source=row.source or f"vat:{row.id}",
            rate=row.rate_override or "0%",
            condition=row.condition or "",
            legal_ref=row.legal_ref,
            note=row.note,
            valid_to=row.valid_to.isoformat() if row.valid_to else None,
        )
        for row in conditional
    ]

    # Меры защиты рынка ЕАЭС: совпадение по коду (и стране, если указана).
    measures = find_code_measures(await get_trade_remedies(db), code10=code, on_date=day)
    if origin:
        measures = [m for m in measures if origin_matches(m.countries, origin)]
    trade_remedies = [
        TradeRemedyInfo(
            measure_no=m.measure_no, kind=m.kind, title=m.title,
            product_description=m.product_description, countries=list(m.countries),
            rates=rate_infos(m),
            valid_to=m.valid_to.isoformat() if m.valid_to else None,
            notes=m.notes, legal_basis=m.legal_basis, source_url=m.source_url,
        )
        for m in measures
    ]

    excise_row = await get_excise_rate(db, code, day)
    if excise_row:
        excise = ExciseInfo(
            applicable=True,
            product_name=excise_row.product_name,
            rate=excise_row.rate,
            rate_unit=excise_row.rate_unit,
            legal_ref=excise_row.legal_ref,
            note=excise_row.note,
        )
    else:
        excise = ExciseInfo(applicable=False)

    ntm = await build_ntm_summary(db, code)

    return CodeLookupResponse(
        code=node.code,
        code_formatted=node.code_formatted or code,
        name=node.name,
        duty_rate=node.duty_rate,
        vat=vat,
        excise=excise,
        unit_primary=node.unit_primary,
        unit_secondary=node.unit_secondary,
        ntm=ntm if ntm.total_count > 0 else None,
        trade_remedies=trade_remedies,
        customs_fee=fee_rule_text(),
    )
