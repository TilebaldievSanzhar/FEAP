"""Логика проверки: Клини, «прочие», числа, единицы, интервалы — на текстах ЕТТ."""
from decimal import Decimal

import pytest

from feap.decision.contracts import EvidenceFact, Status
from feap.decision.logic import and_, compare, not_, or_, parse_interval, parse_number, residual, unit_of

T, F, U = Status.T, Status.F, Status.U


# ── Клини и остаток ─────────────────────────────────────────────────────────

def test_kleene_conjunction_and_disjunction():
    assert and_(T, T) is T and and_(T, U) is U and and_(U, F) is F and and_() is T
    assert or_(F, F) is F and or_(F, U) is U and or_(U, T) is T and or_() is F
    assert [not_(s) for s in (T, F, U)] == [F, T, U]


def test_residual_is_true_only_when_every_preceding_sibling_is_refuted():
    assert residual([]) is T          # «прочие» без конкретных соседей
    assert residual([F, F, F]) is T
    assert residual([F, T, U]) is F   # сосед подтверждён — не «прочие»
    assert residual([F, U]) is U      # ¬U = U: один неизвестный сосед — остаток не установлен


# ── Числа и единицы ────────────────────────────────────────────────────────

@pytest.mark.parametrize('text, value', [
    ('0,35', Decimal('0.35')), ('7.5', Decimal('7.5')), ('1 000', Decimal('1000')),
    ('12 500,5', Decimal('12500.5')), ('750', Decimal('750')),
])
def test_numbers_with_decimal_comma_and_thousands(text, value):
    assert parse_number(text) == value


@pytest.mark.parametrize('text', ['', 'около 5', '5 кг', None, '1,2,3'])
def test_not_a_number(text):
    assert parse_number(text) is None


def test_units_keep_their_physical_meaning():
    assert unit_of('кВт').dimension == 'power' and unit_of('кВт').factor == 1000
    assert unit_of('кВА').dimension == 'apparent_power'  # не мощность в кВт
    assert unit_of('кВт·ч').dimension == 'energy'
    assert unit_of('мВт').factor == Decimal('0.001') and unit_of('МВт').factor == 1_000_000
    assert unit_of('мвт') is None  # без регистра милли- и мега- не различить
    assert unit_of('мас. %').dimension == 'mass_percent' and unit_of('% об.').dimension == 'volume_percent'
    assert unit_of('м³').dimension == 'volume' and unit_of('куб.см').factor == Decimal('0.001')
    assert unit_of('литров').dimension == 'volume' and unit_of('л.').dimension == 'volume'
    assert unit_of('раз') is None and unit_of(None) is None
    assert unit_of('м³/ч').dimension == 'flow' and unit_of('л/мин').factor == Decimal('0.06')
    assert unit_of('кН').dimension == 'force' and unit_of('нм').dimension == 'length'
    assert unit_of('дюйма').factor == Decimal('0.0254') and unit_of('мг/кг').dimension == 'mass_fraction'


# ── Интервалы из строк ЕТТ ─────────────────────────────────────────────────

def _bounds(text):
    quantity, interval = parse_interval(text)
    return quantity, interval.dimension, interval.low, interval.low_inclusive, interval.high, interval.high_inclusive


@pytest.mark.parametrize('text, expected', [
    ('с содержанием жира более 1 мас.%, но не более 6 мас.%',
     ('с содержанием жира', 'mass_percent', 1, False, 6, True)),
    ('не более 3 мас.%', ('', 'mass_percent', None, False, 3, True)),
    ('номинальной выходной мощностью более 750 Вт, но не более 7,5 кВт',
     ('номинальной выходной мощностью', 'power', 750, False, Decimal('7500'), True)),
    ('массой 50 кг или более', ('массой', 'mass', 50, True, None, False)),
    ('2 л или менее', ('', 'volume', None, False, 2, True)),
    ('более 1, но не более 6 мас.%', ('', 'mass_percent', 1, False, 6, True)),
    ('на напряжение более 80 В, но менее 1000 В', ('на напряжение', 'voltage', 80, False, 1000, False)),
    ('в первичных упаковках нетто-объемом не более 2 л', ('в первичных упаковках нетто-объемом', 'volume',
                                                          None, False, 2, True)),
    ('емкостью более 2 л', ('емкостью', 'volume', 2, False, None, False)),
])
def test_whole_row_comparisons_become_intervals(text, expected):
    assert _bounds(text) == expected


@pytest.mark.parametrize('text', [
    # после границы — ещё условие
    'молоко в первичных упаковках нетто-объемом не более 0,35 л, для детского питания',
    # две величины
    'нефть сырая плотностью при 20 °С более 887,6 кг/м3, но не более 994 кг/м3 и с содержанием серы не менее '
    '0,015 мас.%, но не более 3,47 мас.%',
    'автобусы, предназначенные для перевозки более 120 человек, включая водителя',
    'с размером одной стороны не более 435 мм, а другой – не более 297 мм',
    'асинхронные с высотой оси вращения 250 мм',  # равенство, не сравнение
    'промытые не менее 4 раз',                   # не единица
    'провода и кабели с изолированными проводниками диаметром более 0,51 мм',  # длинная «величина»
    'наиболее 5 кг',
])
def test_other_rows_stay_semantic(text):
    assert parse_interval(text) is None


# ── Факт против интервала ──────────────────────────────────────────────────

def _interval(text):
    return parse_interval(text)[1]


def _fact(value, unit, value_max=None):
    return EvidenceFact(attribute='x', value=parse_number(value), value_max=parse_number(value_max), unit=unit)


def test_power_is_compared_in_one_unit_with_strict_and_inclusive_bounds():
    power = _interval('номинальной выходной мощностью более 750 Вт, но не более 7,5 кВт')
    assert compare(power, _fact('3', 'кВт'))[0] is T
    assert compare(power, _fact('750', 'Вт'))[0] is F   # «более 750 Вт» — строго
    assert compare(power, _fact('7,5', 'кВт'))[0] is T  # «не более 7,5 кВт» — включительно
    assert compare(power, _fact('0.75', 'kW'))[0] is F


def test_kva_is_not_kw():
    status, detail, _ = compare(_interval('мощностью не более 37 кВт'), _fact('30', 'кВА'))
    assert status is U and 'другая величина' in detail


def test_plain_percent_is_read_as_the_condition_percent_and_said_so():
    status, _, presumed = compare(_interval('не более 3 мас.%'), _fact('3,2', '%'))
    assert status is F and presumed == ['«%» понят как «мас.%»']


def test_volume_converts_between_ml_and_l():
    assert compare(_interval('емкостью не более 2 л'), _fact('400', 'мл'))[0] is T
    assert compare(_interval('емкостью более 2 л'), _fact('20', 'литров'))[0] is T


def test_range_decides_only_when_it_is_on_one_side():
    power = _interval('мощностью более 0,75 кВт')
    assert compare(power, _fact('0.5', 'кВт', '1'))[0] is U
    assert compare(power, _fact('0.1', 'кВт', '0.5'))[0] is F
    assert compare(power, _fact('1', 'кВт', '2'))[0] is T


def test_missing_value_or_unknown_unit_is_unknown():
    interval = _interval('массой не более 80 кг')
    assert compare(interval, None)[0] is U
    assert compare(interval, EvidenceFact(attribute='масса', note='не указано'))[0] is U
    assert compare(interval, _fact('70', 'пуд'))[0] is U


def test_flow_rate_is_converted_not_guessed():
    """M32: 250 л/мин = 15 м³/ч, это «не более 60 м³/ч»."""
    assert compare(_interval('не более 60 м³/ч'), _fact('250', 'л/мин'))[0] is T
