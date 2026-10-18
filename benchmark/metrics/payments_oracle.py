"""Payments recomputed from declared rates with plain formulas — no product code.

- Customs value (method 1, graph 45): the price and the additions to the EAEU
  border are converted at the NBKR rate and rounded to the tyiyn separately,
  line by line as in the customs value declaration; a documented freight
  after the border is deducted.
- Duty: ad valorem; «X%, но не менее Y EUR за 1 кг» — the larger of the two;
  «Y EUR за 1 пару» — per pair of the supplementary quantity.
- Excise: сом per litre of the supplementary quantity.
- VAT: rate × (customs value + duty + excise), plus remedy duties only when
  the scenario declares so (art. 310 НК КР does not name them).
- Fee: 0.4% of the customs value, not less than 5 and not more than 2500 РП
  (ПП КР № 79, п. 38).
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from benchmark.schema.calc_scenario import Declared
from feap.calc.schemas import CalcIn

_CENT = Decimal('0.01')


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def expected_payments(calc: CalcIn, declared: Declared) -> dict[str, Decimal]:
    """{'customs_value', 'duty', 'excise'?, 'antidumping'?, 'vat', 'fee'} in сом."""
    rate = declared.fx[calc.currency]
    additions = (calc.freight_to_border + calc.insurance + calc.other_charges
                 - calc.freight_beyond_border)
    customs_value = _q(calc.price * rate) + _q(additions * rate)
    out = {'customs_value': customs_value}

    duty = declared.duty
    if duty.eur_per_pair is not None:
        if calc.supp_qty is None:
            raise ValueError('Per-pair duty needs supp_qty')
        out['duty'] = _q(calc.supp_qty * duty.eur_per_pair * declared.fx['EUR'])
    else:
        out['duty'] = _q(customs_value * duty.pct / 100)
        if duty.min_eur_per_kg is not None:
            if calc.net_kg is None:
                raise ValueError("'Не менее … за 1 кг' needs net_kg")
            out['duty'] = max(out['duty'], _q(calc.net_kg * duty.min_eur_per_kg * declared.fx['EUR']))

    excise = Decimal(0)
    if declared.excise_som_per_litre is not None:
        if calc.supp_qty is None:
            raise ValueError('Excise per litre needs supp_qty')
        excise = out['excise'] = _q(calc.supp_qty * declared.excise_som_per_litre)

    remedy = Decimal(0)
    if declared.antidumping_pct is not None:
        remedy = out['antidumping'] = _q(customs_value * declared.antidumping_pct / 100)

    vat_base = customs_value + out['duty'] + excise
    if declared.vat_base_includes_remedies:
        vat_base += remedy
    out['vat'] = _q(vat_base * declared.vat_pct / 100)

    fee = customs_value * declared.fee_pct / 100
    out['fee'] = _q(min(max(fee, declared.fee_min_kgs), declared.fee_max_kgs))
    return out
