"""Справочный снапшот для сборки/проверки ДТ: коды, ставки, курсы — из PostgreSQL.

Снапшот собирается один раз на генерацию драфта (assemble и ФЛК — чистые
и синхронные), реализует протокол flk.RefContext. Все ставки, льготы и меры
берутся на дату расчёта досье (rate_date), а не на «сегодня».

Курсы — только подтверждённые на день (refsync.fx): нет курса → None с
причиной в Rates.issues (needs_input), старый курс не подставляется. Гарантии
остальных справочников (refsync.guard) — в checks: блокирующие уходят в
missing_parameters, предупреждения — в ФЛК (W-REF-ATTEST).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from feap.config import settings
from feap.db.models import RefClassifier, TnvedNode
from feap.db.queries import (
    get_excise_rate,
    get_ntm_measures_for_code,
    get_trade_agreements,
    get_trade_remedies,
    get_vat_rules,
)
from feap.dt.assembler import ItemRef, Rates, VatOption
from feap.dt.flk import CodeInfo
from feap.refsync.ett_sync import ett_snapshot
from feap.refsync.fx import FxResolution, resolve_fx
from feap.refsync.guard import DatasetCheck, attestation_warning, check_datasets
from feap.tariff.agreements import TradeAgreementRef
from feap.tariff.ett import resolve_duty
from feap.tariff.remedies import RemedyMeasure


@dataclass
class RefSnapshot:
    """Реализация flk.RefContext + данные для assemble()."""

    codes: dict[str, CodeInfo] = field(default_factory=dict)
    item_refs: list[ItemRef] = field(default_factory=list)
    rates: Rates | None = None
    classifiers: dict[str, set[str]] = field(default_factory=dict)
    ntm_flags: dict[str, bool] = field(default_factory=dict)
    remedies: list[RemedyMeasure] = field(default_factory=list)
    agreements: list[TradeAgreementRef] = field(default_factory=list)
    checks: list[DatasetCheck] = field(default_factory=list)
    #: Ручной курс введён, но официальный есть и расходится — использован официальный.
    manual_ignored: list[dict] = field(default_factory=list)

    def code_info(self, code10: str) -> CodeInfo | None:
        return self.codes.get(code10)

    def classifier_codes(self, classifier: str) -> set[str] | None:
        return self.classifiers.get(classifier)

    def has_ntm_measures(self, code10: str) -> bool:
        return self.ntm_flags.get(code10, False)

    def refdata_meta(self) -> dict:
        """След справочников для graphs.meta.refdata (ФЛК, интерфейс, PDF)."""
        return {
            "datasets": [c.to_json() for c in self.checks],
            "blocking": [c.blocking for c in self.checks if c.blocking],
            "attestation_warning": attestation_warning(self.checks),
            "manual_fx_ignored": self.manual_ignored,
        }


async def resolve_rates(
    session: AsyncSession,
    *,
    currencies: list[str],
    rate_date: dt.date,
    manual: dict[str, Decimal] | None = None,
) -> tuple[dict[str, Decimal | None], dict[str, str], dict[str, dict], list[dict]]:
    """Курсы на день: (курс | None, причины, след, проигнорированные ручные курсы).

    Ручной курс применяется, только если официальный не подтверждён.
    """
    manual = {k.upper(): v for k, v in (manual or {}).items()}
    values: dict[str, Decimal | None] = {}
    issues: dict[str, str] = {}
    provenance: dict[str, dict] = {}
    ignored: list[dict] = []
    for currency in dict.fromkeys(c.upper() for c in currencies):
        res: FxResolution = await resolve_fx(session, currency, rate_date)
        if res.quote is not None:
            values[currency] = res.quote.rate
            provenance[currency] = res.quote.to_json()
            if currency in manual and manual[currency] != res.quote.rate:
                ignored.append({
                    "currency": currency, "manual": format(manual[currency], "f"),
                    "official": format(res.quote.rate, "f"), "day": rate_date.isoformat(),
                })
        elif currency in manual:
            values[currency] = manual[currency]
            provenance[currency] = {
                "currency": currency, "day": rate_date.isoformat(),
                "rate": format(manual[currency], "f"), "source": "manual",
                "issue": res.issue,
            }
        else:
            values[currency] = None
            if res.issue:
                issues[currency] = res.issue
    return values, issues, provenance, ignored


async def build_ref_snapshot(
    session: AsyncSession,
    *,
    hs_codes: list[str | None],
    contract_currency: str,
    rate_date: dt.date,
    manual_rates: dict[str, Decimal] | None = None,
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
    # Ставки ЕТТ из сводной редакции ЕЭК на дату (refsync.ett_sync); снимок
    # TWS в tnved_tree — только для групп без ЕТТ и дат раньше редакции.
    ett_rows, ett_notes, ett_groups = await ett_snapshot(session, unique_codes)

    per_code_ref: dict[str, ItemRef] = {}
    for code in unique_codes:
        node = nodes.get(code)
        if node is None:
            snapshot.codes[code] = CodeInfo(exists=False, is_leaf=False)
            per_code_ref[code] = ItemRef(code_exists=False, is_leaf=False)
            continue

        duty = resolve_duty(
            code, fallback_rate=node.duty_rate, rows=ett_rows, notes=ett_notes,
            group_versions=ett_groups, day=rate_date,
        )
        snapshot.codes[code] = CodeInfo(
            exists=True, is_leaf=bool(node.is_leaf),
            unit_secondary=node.unit_secondary, duty_raw=duty.rate,
        )

        vat_rate, vat_note = vat_default, None
        unconditional, conditional = await get_vat_rules(session, code, rate_date)
        if unconditional is not None:
            vat_rate = unconditional.rate_override or "0%"
            vat_note = unconditional.legal_ref
        vat_options = tuple(
            VatOption(
                source=row.source or f"vat:{row.id}",
                rate=row.rate_override or "0%",
                condition=row.condition or "",
                legal_ref=row.legal_ref,
                note=row.note,
            )
            for row in conditional
        )

        excise_amount: Decimal | None = None
        excise_unit: str | None = None
        excise = await get_excise_rate(session, code, rate_date)
        if excise is not None:
            try:
                excise_amount = Decimal(excise.rate)
                excise_unit = excise.rate_unit
            except Exception:  # noqa: BLE001 — нечисловая ставка → ручной расчёт
                excise_amount, excise_unit = None, None

        per_code_ref[code] = ItemRef(
            code_exists=True, is_leaf=bool(node.is_leaf),
            duty_raw=duty.rate, duty_trace=duty.trace, duty_issue=duty.issue,
            vat_rate=vat_rate, vat_note=vat_note,
            excise_amount=excise_amount, excise_unit=excise_unit,
            unit_secondary=node.unit_secondary,
            vat_options=vat_options,
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

    # Меры защиты рынка и торговые режимы — небольшие таблицы, целиком.
    snapshot.remedies = await get_trade_remedies(session)
    snapshot.agreements = await get_trade_agreements(session)

    contract = contract_currency.upper()
    values, issues, provenance, ignored = await resolve_rates(
        session, currencies=[contract, "USD", "EUR"], rate_date=rate_date, manual=manual_rates,
    )
    snapshot.rates = Rates(
        rate_date=rate_date,
        contract=values[contract],
        usd=values["USD"],
        eur=values["EUR"],
        issues=issues,
        provenance=provenance,
    )
    snapshot.manual_ignored = ignored
    snapshot.checks = await check_datasets(session, rate_date)
    return snapshot
