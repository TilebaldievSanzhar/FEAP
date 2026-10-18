"""Payment scenarios: the oracle is right by hand, and the runner attributes mismatches."""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

from benchmark.metrics.payments_oracle import expected_payments
from benchmark.schema.calc_scenario import CalcScenario, Declared, DeclaredDuty
from benchmark.scripts.run_calc_scenarios import check
from feap.calc.schemas import CalcIn

FX = {'USD': Decimal('87.45'), 'EUR': Decimal('97.8959')}


def calc(**over):
    base = dict(hs_code='7604210000', price=Decimal('10000'), currency='USD', incoterms='CPT',
                net_kg=Decimal('900'), rate_date=dt.date(2026, 10, 15))
    base.update(over)
    return CalcIn(**base)


def declared(duty=DeclaredDuty(text='12%', pct=Decimal('12')), vat_pct=Decimal('12'), **over):
    return Declared(fx=FX, duty=duty, vat_pct=vat_pct, **over)


def test_oracle_matches_hand_calculation_with_additions():
    # 10 000 × 87,45 + 830 × 87,45 = 874 500 + 72 583,50
    out = expected_payments(calc(incoterms='FOB', freight_to_border=Decimal('800'),
                                 insurance=Decimal('30')), declared())
    assert out == {
        'customs_value': Decimal('947083.50'), 'duty': Decimal('113650.02'),
        'vat': Decimal('127288.02'), 'fee': Decimal('3788.33'),
    }


def test_oracle_duty_forms_and_fee_bounds():
    beef = DeclaredDuty(text='50%, но не менее 1 EUR за 1 кг', pct=Decimal('50'),
                        min_eur_per_kg=Decimal('1'))
    cheap = expected_payments(calc(price=Decimal('1000')), declared(beef, vat_pct=Decimal('0')))
    assert cheap['duty'] == Decimal('88106.31')    # 900 кг × 1 € × 97,8959 > 50% × 87 450
    assert cheap['fee'] == Decimal('500.00')       # 0,4% = 349,80 < 5 РП
    pairs = DeclaredDuty(text='0.75 EUR за 1 пар', eur_per_pair=Decimal('0.75'))
    shoes = expected_payments(calc(supp_qty=Decimal('1000')), declared(pairs))
    assert shoes['duty'] == Decimal('73421.93')    # 73 421,925 → half up
    big = expected_payments(calc(price=Decimal('8000000')), declared())
    assert big['fee'] == Decimal('250000.00')


def test_oracle_keeps_remedies_out_of_vat_base_unless_declared():
    out = expected_payments(calc(), declared(DeclaredDuty(text='9%', pct=Decimal('9')),
                                             antidumping_pct=Decimal('21.89')))
    assert out['antidumping'] == Decimal('191428.05')
    assert out['vat'] == Decimal('114384.60')      # (874 500 + 78 705) × 12%
    included = expected_payments(calc(), declared(
        DeclaredDuty(text='9%', pct=Decimal('9')), antidumping_pct=Decimal('21.89'),
        vat_base_includes_remedies=True))
    assert included['vat'] == Decimal('137355.97')  # (874 500 + 78 705 + 191 428,05) × 12%


def test_duty_must_have_exactly_one_form():
    with pytest.raises(ValueError):
        DeclaredDuty(text='?')
    with pytest.raises(ValueError):
        DeclaredDuty(text='?', pct=Decimal('5'), eur_per_pair=Decimal('1'))


def engine_out(duty_rate='12%', duty='104940.00', status='ready', missing=()):
    line = lambda kind, rate, amount: SimpleNamespace(  # noqa: E731
        kind=kind, rate=rate, amount=None if amount is None else Decimal(amount))
    return SimpleNamespace(
        status=status, missing_parameters=list(missing), customs_value=Decimal('874500.00'),
        payments=[line('duty', duty_rate, duty), line('vat', '12%', '117532.80'),
                  line('fee', '0.4%', '3498.00')],
    )


def scenario(**over):
    base = dict(id='S', title='t', checks='c', calc=calc(), declared=declared(),
                expected_status='ready', verification='source_reviewed')
    base.update(over)
    return CalcScenario(**base)


def test_runner_passes_matching_engine_output():
    assert check(scenario(), engine_out())['ok']


def test_other_rate_in_reference_data_is_attributed_to_data():
    result = check(scenario(), engine_out(duty_rate='10%', duty='87450.00'))
    causes = {p['what']: p['cause'] for p in result['problems']}
    assert not result['ok']
    assert causes['duty rate'] == 'reference data' and causes['duty'] == 'reference data'


def test_same_rate_other_amount_is_attributed_to_engine():
    result = check(scenario(), engine_out(duty='104940.01'))
    assert [p['cause'] for p in result['problems']] == ['engine']


def test_status_and_missing_data_are_checked():
    asked = scenario(declared=None, expected_status='needs_input', expected_missing=['пар'])
    assert check(asked, engine_out(status='needs_input', missing=['количество пар']))['ok']
    result = check(asked, engine_out(status='ready'))
    assert {p['what'] for p in result['problems']} == {'status', 'missing'}
