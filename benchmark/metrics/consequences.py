"""What a code means in money and paperwork, computed by the product's own engine.

An error at the tenth digit can be free (same rates, same measures) or it can
change the duty, bring an anti-dumping duty or drop a permit. Payments come
from calc.run_calc — the engine behind the product passport and the draft
declaration — so a wrong code is priced exactly as a user would see it.

Trade remedies are compared as sets, not as money: whether a measure applies
depends on the product description and is decided by the declarant
(remedy_decisions). Payments are computed with every matched measure set to
'not_applicable', and the measures themselves are reported next to them.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from benchmark.schema.case_v2 import Lot
from feap.calc.engine import run_calc
from feap.calc.schemas import CalcIn, CalcOut
from feap.db.queries import get_leaf_by_code, get_ntm_measures_for_code
from feap.dt.schemas import RemedyDecision

#: Typical import lot from China: $10 000, carriage paid to the EAEU border,
#: 1000 pieces, 900 kg net. Only the difference between two codes matters.
STANDARD_LOT = Lot(
    price=Decimal('10000'), currency='USD', incoterms='CPT', origin_country='CN',
    qty=Decimal('1000'), qty_unit='шт', net_kg=Decimal('900'), gross_kg=Decimal('1000'),
)

#: Supplementary units (graph 41) the standard lot can fill from its quantity.
#: м², л, см³ … depend on the product and stay unknown.
PIECE_UNITS: frozenset[str] = frozenset({'шт', 'пар'})

_RATE_KINDS = ('duty', 'vat', 'excise')


@dataclass
class CodeConsequences:
    code: str
    exists: bool
    status: str | None = None
    rates: dict[str, str] = field(default_factory=dict)       # duty / vat / excise
    payments: dict[str, Decimal] = field(default_factory=dict)  # kind[:measure] → сом
    total: Decimal | None = None   # None if any payment line is not computed
    missing: list[str] = field(default_factory=list)
    remedies: list[str] = field(default_factory=list)  # measures matching code + origin
    ntm: list[str] = field(default_factory=list)
    supp_unit: str | None = None

    def to_json(self) -> dict:
        return {
            'code': self.code, 'exists': self.exists, 'status': self.status,
            'rates': self.rates, 'payments': {k: str(v) for k, v in self.payments.items()},
            'total': None if self.total is None else str(self.total),
            'missing': self.missing, 'remedies': self.remedies, 'ntm': self.ntm,
            'supp_unit': self.supp_unit,
        }


def lot_to_calc(code: str, lot: Lot, *, rate_date: dt.date, supp_unit: str | None) -> CalcIn:
    supp_qty, unit = lot.supp_qty, lot.supp_unit
    if supp_qty is None and supp_unit in PIECE_UNITS and lot.qty is not None:
        supp_qty, unit = lot.qty, supp_unit
    return CalcIn(
        hs_code=code, rate_date=rate_date, price=lot.price, currency=lot.currency,
        incoterms=lot.incoterms, origin_country=lot.origin_country,
        manufacturer=lot.manufacturer, qty=lot.qty, qty_unit=lot.qty_unit,
        net_kg=lot.net_kg, gross_kg=lot.gross_kg, supp_qty=supp_qty, supp_unit=unit,
    )


def _payments(out: CalcOut) -> tuple[dict[str, Decimal], Decimal | None, dict[str, str]]:
    payments: dict[str, Decimal] = {}
    rates: dict[str, str] = {}
    computed = True
    for line in out.payments:
        key = line.kind if line.kind in _RATE_KINDS or not line.ref else f'{line.kind}:{line.ref}'
        if line.kind in _RATE_KINDS:
            rates[line.kind] = line.rate
        if line.amount is None:
            computed = False
            continue
        payments[key] = payments.get(key, Decimal(0)) + line.amount
    total = sum(payments.values(), Decimal(0)) if computed else None
    return payments, total, rates


async def code_consequences(
    session: AsyncSession, code: str, lot: Lot, *, rate_date: dt.date,
) -> CodeConsequences:
    leaf = await get_leaf_by_code(session, code)
    if leaf is None:
        return CodeConsequences(code=code, exists=False)
    calc = lot_to_calc(code, lot, rate_date=rate_date, supp_unit=leaf.unit_secondary)
    out = await run_calc(session, calc)
    remedies = [r['measure_no'] for r in out.tariff.get('remedies') or []]
    if remedies:
        decisions = {m: RemedyDecision(decision='not_applicable') for m in remedies}
        out = await run_calc(session, calc.model_copy(update={'remedy_decisions': decisions}))
    payments, total, rates = _payments(out)
    ntm_rows = await get_ntm_measures_for_code(session, code, on_date=rate_date)
    return CodeConsequences(
        code=code, exists=True, status=out.status, rates=rates, payments=payments,
        total=total, missing=list(out.missing_parameters), remedies=sorted(set(remedies)),
        ntm=sorted({f'{mtype.name_ru}: {measure.name}' for measure, _, mtype in ntm_rows}),
        supp_unit=leaf.unit_secondary,
    )


@dataclass
class ConsequenceDiff:
    """Predicted vs gold. delta < 0 means the declarant would underpay."""

    kind: str  # same | paperwork | money | unknown | invalid_code | gold_invalid | gold_partial
    delta_total: Decimal | None = None
    delta_by_kind: dict[str, Decimal] = field(default_factory=dict)
    remedies_missed: list[str] = field(default_factory=list)
    remedies_extra: list[str] = field(default_factory=list)
    ntm_missed: list[str] = field(default_factory=list)
    ntm_extra: list[str] = field(default_factory=list)
    supp_unit_changed: bool = False

    def to_json(self) -> dict:
        return {
            'kind': self.kind,
            'delta_total': None if self.delta_total is None else str(self.delta_total),
            'delta_by_kind': {k: str(v) for k, v in self.delta_by_kind.items()},
            'remedies_missed': self.remedies_missed, 'remedies_extra': self.remedies_extra,
            'ntm_missed': self.ntm_missed, 'ntm_extra': self.ntm_extra,
            'supp_unit_changed': self.supp_unit_changed,
        }


def compare_consequences(gold: CodeConsequences, pred: CodeConsequences) -> ConsequenceDiff:
    if not gold.exists:
        return ConsequenceDiff(kind='gold_invalid')
    if not pred.exists:
        return ConsequenceDiff(kind='invalid_code')
    delta_total = None
    keys = set(gold.payments) & set(pred.payments)
    if gold.total is not None and pred.total is not None:
        delta_total = pred.total - gold.total
        # Both fully computed: a line present for one code only is a real difference.
        keys = set(gold.payments) | set(pred.payments)
    delta_by_kind = {
        k: pred.payments.get(k, Decimal(0)) - gold.payments.get(k, Decimal(0))
        for k in sorted(keys)
        if pred.payments.get(k, Decimal(0)) != gold.payments.get(k, Decimal(0))
    }
    diff = ConsequenceDiff(
        kind='same', delta_total=delta_total, delta_by_kind=delta_by_kind,
        remedies_missed=sorted(set(gold.remedies) - set(pred.remedies)),
        remedies_extra=sorted(set(pred.remedies) - set(gold.remedies)),
        ntm_missed=sorted(set(gold.ntm) - set(pred.ntm)),
        ntm_extra=sorted(set(pred.ntm) - set(gold.ntm)),
        supp_unit_changed=gold.supp_unit != pred.supp_unit,
    )
    if delta_by_kind or diff.remedies_missed or diff.remedies_extra:
        diff.kind = 'money'
    elif delta_total is None:
        diff.kind = 'unknown'
    elif diff.ntm_missed or diff.ntm_extra or diff.supp_unit_changed:
        diff.kind = 'paperwork'
    return diff
