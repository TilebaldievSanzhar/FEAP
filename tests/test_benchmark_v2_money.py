"""Benchmark v2: pricing a wrong code against the gold one (pure parts)."""
import datetime as dt
from decimal import Decimal

from benchmark.evaluator.fingerprint import libpq_dsn
from benchmark.metrics.consequences import (
    STANDARD_LOT,
    CodeConsequences,
    compare_consequences,
    lot_to_calc,
)

DAY = dt.date(2026, 10, 15)


def priced(code, total='100.00', remedies=(), ntm=('ТР ТС 004/2011',), unit=None, **payments):
    lines = {k: Decimal(v) for k, v in (payments or {'duty': '50.00', 'vat': '50.00'}).items()}
    return CodeConsequences(
        code=code, exists=True, status='ready', payments=lines,
        total=None if total is None else Decimal(total),
        remedies=list(remedies), ntm=list(ntm), supp_unit=unit,
    )


def test_payment_difference_is_signed_and_split_by_kind():
    diff = compare_consequences(
        priced('7604210000', '225970.80', duty='104940.00', vat='117532.80', fee='3498.00'),
        priced('7610909000', '206382.00', duty='87450.00', vat='115434.00', fee='3498.00'),
    )
    assert diff.kind == 'money'
    assert diff.delta_total == Decimal('-19588.80')  # underpayment
    assert diff.delta_by_kind == {'duty': Decimal('-17490.00'), 'vat': Decimal('-2098.80')}


def test_same_money_but_other_measures_is_paperwork():
    diff = compare_consequences(priced('A'), priced('B', ntm=()))
    assert diff.kind == 'paperwork' and diff.ntm_missed == ['ТР ТС 004/2011']
    assert compare_consequences(priced('A'), priced('B', unit='шт')).kind == 'paperwork'
    assert compare_consequences(priced('A'), priced('B')).kind == 'same'


def test_lost_trade_remedy_counts_as_money_even_with_equal_payments():
    diff = compare_consequences(priced('A', remedies=['AD-32']), priced('B'))
    assert diff.kind == 'money' and diff.remedies_missed == ['AD-32']


def test_not_computed_payments_are_unknown_not_zero():
    diff = compare_consequences(priced('A'), priced('B', total=None, duty='50.00'))
    assert diff.kind == 'unknown' and diff.delta_total is None
    # A line computed for one code only is not a difference when totals are unknown.
    assert diff.delta_by_kind == {}


def test_invalid_codes():
    missing = CodeConsequences(code='0000000000', exists=False)
    assert compare_consequences(priced('A'), missing).kind == 'invalid_code'
    assert compare_consequences(missing, priced('A')).kind == 'gold_invalid'


def test_standard_lot_fills_pieces_but_not_litres():
    pieces = lot_to_calc('8501522001', STANDARD_LOT, rate_date=DAY, supp_unit='шт')
    assert pieces.supp_qty == Decimal('1000') and pieces.supp_unit == 'шт'
    litres = lot_to_calc('2204219800', STANDARD_LOT, rate_date=DAY, supp_unit='л')
    assert litres.supp_qty is None
    assert litres.price == Decimal('10000') and litres.origin_country == 'CN'
    assert litres.rate_date == DAY


def test_libpq_dsn_drops_the_sqlalchemy_driver():
    dsn = libpq_dsn('postgresql+asyncpg://feap:p%40ss@localhost:5433/feap')
    assert dsn == 'postgresql://feap:p%40ss@localhost:5433/feap'
    assert libpq_dsn('postgresql+psycopg2://u:p@db/x').startswith('postgresql://')
