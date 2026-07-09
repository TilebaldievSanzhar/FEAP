"""Справочный снапшот для сборки/проверки ДТ: коды, ставки, курсы — из PostgreSQL.

Снапшот собирается один раз на генерацию драфта (assemble и ФЛК — чистые
и синхронные), реализует протокол flk.RefContext.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from feap.config import settings
from feap.db.models import ExchangeRate, RefClassifier, TnvedNode
from feap.db.queries import (
    get_excise_rate,
    get_ntm_measures_for_code,
    get_vat_exemption,
)
from feap.dt.assembler import ItemRef, Rates
from feap.dt.flk import CodeInfo


@dataclass
class RefSnapshot:
    """Реализация flk.RefContext + данные для assemble()."""

    codes: dict[str, CodeInfo] = field(default_factory=dict)
    item_refs: list[ItemRef] = field(default_factory=list)
    rates: Rates | None = None
    classifiers: dict[str, set[str]] = field(default_factory=dict)
    ntm_flags: dict[str, bool] = field(default_factory=dict)

    def code_info(self, code10: str) -> CodeInfo | None:
        return self.codes.get(code10)

    def classifier_codes(self, classifier: str) -> set[str] | None:
        return self.classifiers.get(classifier)

    def has_ntm_measures(self, code10: str) -> bool:
        return self.ntm_flags.get(code10, False)


async def get_fx_rate(
    session: AsyncSession, currency: str, on_date: dt.date
) -> Decimal | None:
    """Курс НБКР: последняя запись с датой ≤ on_date (курсы публикуются по будням)."""
    if currency == "KGS":
        return Decimal(1)
    row = await session.execute(
        select(ExchangeRate.rate)
        .where(ExchangeRate.currency == currency, ExchangeRate.rate_date <= on_date)
        .order_by(ExchangeRate.rate_date.desc())
        .limit(1)
    )
    value = row.scalar_one_or_none()
    return Decimal(value) if value is not None else None


async def build_ref_snapshot(
    session: AsyncSession,
    *,
    hs_codes: list[str | None],
    contract_currency: str,
    rate_date: dt.date,
) -> RefSnapshot:
    """Собрать снапшот по позициям досье (порядок item_refs = порядку hs_codes)."""
    snapshot = RefSnapshot()

    unique_codes = sorted({c for c in hs_codes if c})
    nodes: dict[str, TnvedNode] = {}
    if unique_codes:
        result = await session.execute(
            select(TnvedNode).where(TnvedNode.code.in_(unique_codes))
        )
        for node in result.scalars().all():
            # При дублях (код встречается на нескольких уровнях) приоритет — листу.
            existing = nodes.get(node.code)
            if existing is None or (node.is_leaf and not existing.is_leaf):
                nodes[node.code] = node

    vat_default = settings.VAT_RATE_DEFAULT

    per_code_ref: dict[str, ItemRef] = {}
    for code in unique_codes:
        node = nodes.get(code)
        if node is None:
            snapshot.codes[code] = CodeInfo(exists=False, is_leaf=False)
            per_code_ref[code] = ItemRef(code_exists=False, is_leaf=False)
            continue

        snapshot.codes[code] = CodeInfo(
            exists=True, is_leaf=bool(node.is_leaf),
            unit_secondary=node.unit_secondary, duty_raw=node.duty_rate,
        )

        vat_rate, vat_note = vat_default, None
        exemption = await get_vat_exemption(session, code)
        if exemption is not None:
            vat_rate = exemption.rate_override or "0%"
            vat_note = exemption.legal_ref

        excise_amount: Decimal | None = None
        excise_unit: str | None = None
        excise = await get_excise_rate(session, code)
        if excise is not None:
            try:
                excise_amount = Decimal(excise.rate)
                excise_unit = excise.rate_unit
            except Exception:  # noqa: BLE001 — нечисловая ставка → ручной расчёт
                excise_amount, excise_unit = None, None

        per_code_ref[code] = ItemRef(
            code_exists=True, is_leaf=bool(node.is_leaf),
            duty_raw=node.duty_rate,
            vat_rate=vat_rate, vat_note=vat_note,
            excise_amount=excise_amount, excise_unit=excise_unit,
            unit_secondary=node.unit_secondary,
        )

    for code in hs_codes:
        if code and code in per_code_ref:
            snapshot.item_refs.append(per_code_ref[code])
        else:
            # Без кода: НДС по умолчанию, пошлину посчитать нельзя.
            snapshot.item_refs.append(ItemRef(vat_rate=vat_default))

    # Классификаторы Решения 378 — вся таблица одним запросом (она маленькая).
    # Отсутствующий классификатор → None в classifier_codes → ФЛК F-09 skip.
    result = await session.execute(
        select(RefClassifier.classifier_code, RefClassifier.item_code)
    )
    for classifier_code, item_code in result.all():
        snapshot.classifiers.setdefault(classifier_code, set()).add(item_code)

    # Флаги «есть меры НТР» по кодам позиций (для F-14).
    for code in unique_codes:
        rows = await get_ntm_measures_for_code(session, code)
        snapshot.ntm_flags[code] = bool(rows)

    snapshot.rates = Rates(
        rate_date=rate_date,
        contract=await get_fx_rate(session, contract_currency, rate_date),
        usd=await get_fx_rate(session, "USD", rate_date),
        eur=await get_fx_rate(session, "EUR", rate_date),
    )
    return snapshot
