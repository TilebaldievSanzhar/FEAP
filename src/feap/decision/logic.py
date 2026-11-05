"""Логика проверки: Клини, «прочие», числа, единицы, интервалы.

Чистые функции без модели и без БД (Дизайн B, §6.3; план, §5.2):

- конъюнкция пути — по Клини: F, если есть F; T, если все T; иначе U;
- «прочие» в своём scope = ¬s1 ∧ … ∧ ¬sk по *предшествующим* конкретным соседям;
  ¬U = U, поэтому «прочие» истинно, только если все такие соседи опровергнуты;
- числа — ``Decimal`` с десятичной запятой; границы «более / не более» строгие и
  нестрогие как в тексте ЕТТ;
- единицы переводятся только внутри одной величины: кВА — полная мощность и в кВт
  не переводится, кВт·ч — энергия, а не мощность.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from feap.decision.contracts import EvidenceFact, Interval, Status

T, F, U = Status.T, Status.F, Status.U


# ── Клини ──────────────────────────────────────────────────────────────────

def and_(*statuses: Status) -> Status:
    if F in statuses:
        return F
    if U in statuses:
        return U
    return T


def or_(*statuses: Status) -> Status:
    if T in statuses:
        return T
    if U in statuses:
        return U
    return F


def not_(status: Status) -> Status:
    return {T: F, F: T, U: U}[status]


def residual(preceding: Iterable[Status]) -> Status:
    """«Прочие»: ни один предшествующий конкретный сосед не выполнен. Без соседей — T."""
    return not_(or_(*preceding))


# ── Числа и единицы ────────────────────────────────────────────────────────

#: Число как в тексте ЕТТ и описаний: «0,35», «7.5», «1 000», «12 500,5».
NUMBER = r'\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?(?!\d)|\d+(?:[.,]\d+)?'
_NUMBER_ONLY = re.compile(rf'(?:{NUMBER})')


def parse_number(text: str | None) -> Decimal | None:
    """«0,35» → 0.35; «1 000» → 1000; не число целиком → None."""
    if text is None:
        return None
    value = str(text).strip()
    if not _NUMBER_ONLY.fullmatch(value):
        return None
    try:
        return Decimal(re.sub(r'[   ]', '', value).replace(',', '.'))
    except InvalidOperation:
        return None


@dataclass(frozen=True)
class Unit:
    dimension: str
    factor: Decimal  # множитель к базовой единице величины
    base: str


def _units(dimension: str, base: str, aliases: dict[str, str]) -> dict[str, Unit]:
    return {alias: Unit(dimension, Decimal(factor), base) for alias, factor in aliases.items()}


#: Псевдонимы в нижнем регистре, без пробелов, ² и ³ заменены цифрами.
_UNITS: dict[str, Unit] = {
    **_units('mass', 'кг', {'мг': '0.000001', 'mg': '0.000001', 'г': '0.001', 'гр': '0.001', 'g': '0.001',
                            'кг': '1', 'kg': '1', 'ц': '100', 'т': '1000', 'тонн': '1000', 'тонны': '1000',
                            'тонна': '1000'}),
    **_units('volume', 'л', {'мл': '0.001', 'ml': '0.001', 'см3': '0.001', 'куб.см': '0.001', 'cm3': '0.001',
                             'cc': '0.001', 'л': '1', 'l': '1', 'литр': '1', 'литра': '1', 'литров': '1',
                             'дм3': '1', 'м3': '1000', 'куб.м': '1000', 'm3': '1000'}),
    **_units('length', 'м', {'нм': '0.000000001', 'нанометров': '0.000000001', 'nm': '0.000000001',
                             'мкм': '0.000001', 'µm': '0.000001', 'мм': '0.001', 'mm': '0.001',
                             'см': '0.01', 'cm': '0.01', 'дм': '0.1', 'м': '1', 'm': '1', 'км': '1000',
                             'km': '1000', 'дюйм': '0.0254', 'дюйма': '0.0254', 'дюймов': '0.0254',
                             'inch': '0.0254', '"': '0.0254'}),
    **_units('area', 'м2', {'мм2': '0.000001', 'кв.мм': '0.000001', 'mm2': '0.000001', 'см2': '0.0001',
                            'м2': '1', 'кв.м': '1', 'm2': '1'}),
    **_units('power', 'Вт', {'вт': '1', 'w': '1', 'квт': '1000', 'kw': '1000', 'л.с': '735.49875',
                             'hp': '745.69987158227022'}),
    **_units('apparent_power', 'В·А', {'ва': '1', 'в·а': '1', 'va': '1', 'ква': '1000', 'кв·а': '1000',
                                       'kva': '1000', 'мва': '1000000', 'mva': '1000000'}),
    **_units('voltage', 'В', {'в': '1', 'v': '1', 'кв': '1000', 'kv': '1000'}),
    **_units('current', 'А', {'а': '1', 'a': '1', 'ка': '1000', 'ka': '1000'}),
    **_units('frequency', 'Гц', {'гц': '1', 'hz': '1', 'кгц': '1000', 'khz': '1000', 'мгц': '1000000',
                                 'mhz': '1000000', 'ггц': '1000000000', 'ghz': '1000000000'}),
    **_units('pressure', 'Па', {'па': '1', 'pa': '1', 'кпа': '1000', 'kpa': '1000', 'мпа': '1000000',
                                'mpa': '1000000', 'бар': '100000', 'bar': '100000'}),
    **_units('mass_percent', 'мас.%', {'мас.%': '1', 'масс.%': '1', '%мас.': '1', '%мас': '1',
                                       '%масс.': '1', '%масс': '1', '%(мас.)': '1', 'wt%': '1'}),
    **_units('volume_percent', 'об.%', {'об.%': '1', '%об.': '1', '%об': '1', '%vol': '1', 'vol%': '1',
                                        '%vol.': '1'}),
    **_units('percent', '%', {'%': '1', 'процент': '1', 'процента': '1', 'процентов': '1'}),
    **_units('density', 'кг/м3', {'кг/м3': '1', 'kg/m3': '1', 'г/см3': '1000', 'g/cm3': '1000',
                                  'кг/л': '1000', 'г/л': '1', 'г/мл': '1000'}),
    **_units('surface_density', 'г/м2', {'г/м2': '1', 'g/m2': '1', 'кг/м2': '1000', 'gsm': '1'}),
    **_units('linear_density', 'дтекс', {'дтекс': '1', 'dtex': '1', 'текс': '10', 'tex': '10'}),
    **_units('temperature', '°С', {'°с': '1', '°c': '1', 'градусов': '1', 'градуса': '1'}),
    **_units('years', 'год', {'год': '1', 'года': '1', 'лет': '1'}),
    **_units('rotation', 'об/мин', {'об/мин': '1', 'rpm': '1'}),
    **_units('count', 'шт', {'шт': '1', 'штук': '1', 'штуки': '1', 'pcs': '1', 'человек': '1', 'чел': '1',
                             'мест': '1', 'места': '1', 'место': '1'}),
    **_units('energy', 'Вт·ч', {'вт·ч': '1', 'wh': '1', 'квт·ч': '1000', 'kwh': '1000'}),
    **_units('charge', 'А·ч', {'а·ч': '1', 'ah': '1', 'ма·ч': '0.001', 'мач': '0.001', 'mah': '0.001'}),
    **_units('speed', 'км/ч', {'км/ч': '1', 'км/час': '1', 'km/h': '1', 'м/с': '3.6', 'm/s': '3.6',
                               'мм/с': '0.0036'}),
    **_units('torque', 'Н·м', {'н·м': '1', 'н·м.': '1'}),
    **_units('flow', 'м3/ч', {'м3/ч': '1', 'м3/час': '1', 'm3/h': '1', 'м3/мин': '60', 'л/мин': '0.06',
                              'l/min': '0.06', 'л/ч': '0.001', 'л/час': '0.001', 'л/с': '3.6'}),
    **_units('force', 'Н', {'н': '1', 'кн': '1000', 'kn': '1000', 'мн': '1000000'}),
    **_units('mass_fraction', 'мг/кг', {'мг/кг': '1', 'mg/kg': '1', 'ppm': '1', 'г/кг': '1000',
                                        'г/т': '1'}),
    **_units('twist', 'кр/м', {'кр/м': '1'}),
    **_units('reactive_power', 'вар', {'вар': '1', 'квар': '1000', 'kvar': '1000'}),
}
#: Регистр различает приставки: мВт — милливатт, МВт — мегаватт.
_CASED: dict[str, Unit] = {
    **_units('power', 'Вт', {'мВт': '0.001', 'mW': '0.001', 'МВт': '1000000', 'MW': '1000000'}),
    **_units('current', 'А', {'мА': '0.001', 'mA': '0.001'}),
    **_units('voltage', 'В', {'мВ': '0.001', 'mV': '0.001'}),
}
_AMBIGUOUS = {'мвт', 'mw', 'ма', 'ma', 'мв', 'mv'}  # без регистра приставку не понять


def unit_of(raw: str | None) -> Unit | None:
    """Единица по написанию: «кВт», «мас. %», «куб.см», «м³». Неизвестная → None."""
    if not raw:
        return None
    text = re.sub(r'\s+', '', str(raw).replace('²', '2').replace('³', '3'))
    text = text.replace('×', '·').replace('*', '·')
    if text in _CASED:
        return _CASED[text]
    key = text.lower().replace('ё', 'е')
    if key in _AMBIGUOUS:
        return None
    return _UNITS.get(key) or _UNITS.get(key.rstrip('.'))


# ── Интервалы из текста строки ─────────────────────────────────────────────

_UNIT_TOKEN = r'[^\s,;:()]+'
_PREFIX = re.compile(
    rf'(?<![А-Яа-яЁё])(?:(?P<neg>не)\s+)?(?P<op>более|менее|свыше|больше|меньше)\s+'
    rf'(?P<num>{NUMBER})(?:\s*(?P<unit>{_UNIT_TOKEN}))?',
)
_POSTFIX = re.compile(
    rf'(?P<num>{NUMBER})(?:\s*(?P<unit>{_UNIT_TOKEN}))?\s+или\s+(?P<op>более|менее|больше|меньше)'
    r'(?![А-Яа-яЁё])',
)
_GREATER = {'более', 'свыше', 'больше'}
#: Величина перед границей: «с содержанием жира», «номинальной выходной мощностью».
MAX_QUANTITY_WORDS = 4


@dataclass(frozen=True)
class Bound:
    start: int
    end: int
    op: str  # '>', '>=', '<', '<='
    number: str
    unit: str | None


def bounds(text: str) -> list[Bound]:
    """Сравнения с числом в порядке текста: «не более 3 мас.%», «50 кг или более»."""
    found: list[Bound] = []
    for m in _PREFIX.finditer(text):
        greater = m['op'] in _GREATER
        op = ('<=' if greater else '>=') if m['neg'] else ('>' if greater else '<')
        found.append(Bound(m.start(), m.end(), op, m['num'], m['unit']))
    for m in _POSTFIX.finditer(text):
        op = '>=' if m['op'] in _GREATER else '<='
        found.append(Bound(m.start(), m.end(), op, m['num'], m['unit']))
    found.sort(key=lambda b: b.start)
    kept: list[Bound] = []
    for b in found:
        if kept and b.start < kept[-1].end:
            continue  # «не более 2 л или менее»: одно сравнение, а не два
        kept.append(b)
    return kept


def parse_interval(text: str) -> tuple[str, Interval] | None:
    """Строка, которая вся — числовое условие → (величина, интервал); иначе None.

    «с содержанием жира более 1 мас.%, но не более 6 мас.%» → («с содержанием жира», (1; 6]).
    Единица одной границы переходит на другую: «более 1, но не более 6 мас.%».
    Не разбирается и остаётся семантическим условием:

    - после границ есть текст: «не более 10% от общей массы волокна»;
    - две разные величины: «плотностью … и с содержанием серы …»;
    - в величине есть число или она длиннее четырёх слов;
    - неизвестная единица или граница без единицы у обеих сторон.
    """
    found = bounds(text)
    if not 1 <= len(found) <= 2:
        return None
    quantity = text[:found[0].start].strip().rstrip(',').strip()
    if re.search(r'[\d,;:]', quantity) or len(quantity.split()) > MAX_QUANTITY_WORDS:
        return None
    if len(found) == 2 and not re.fullmatch(r'\s*,?\s*(?:но|а|и)?\s*', text[found[0].end:found[1].start]):
        return None
    if text[found[-1].end:].strip(' .'):
        return None
    units = [unit_of(b.unit) if b.unit else None for b in found]
    if any(b.unit and u is None for b, u in zip(found, units)):
        return None
    known = [u for u in units if u]
    if not known or len({u.dimension for u in known}) != 1:
        return None
    low = high = None
    low_inclusive = high_inclusive = False
    for b, unit in zip(found, (u or known[0] for u in units)):
        value = parse_number(b.number)
        if value is None:
            return None
        value *= unit.factor
        if b.op in ('>', '>='):
            if low is not None:
                return None
            low, low_inclusive = value, b.op == '>='
        else:
            if high is not None:
                return None
            high, high_inclusive = value, b.op == '<='
    if low is not None and high is not None and low > high:
        return None
    unit = known[0]
    return quantity, Interval(
        dimension=unit.dimension, unit=unit.base, low=low, low_inclusive=low_inclusive,
        high=high, high_inclusive=high_inclusive, text=text[found[0].start:found[-1].end],
    )


# ── Сравнение факта с интервалом ───────────────────────────────────────────

_PERCENTS = {'mass_percent', 'volume_percent'}


def compare(interval: Interval, fact: EvidenceFact | None) -> tuple[Status, str, list[str]]:
    """Факт из описания против интервала условия → (статус, пояснение, презумпции).

    Другая величина (кВА против кВт, потребляемая против выходной, если модель
    вернула не ту) даёт U, а не F: перевод без дополнительных сведений невозможен.
    """
    if fact is None or fact.value is None:
        return U, 'значение не указано', []
    unit = unit_of(fact.unit)
    if unit is None:
        return U, f'единица не распознана: {fact.unit!r}', []
    presumed: list[str] = []
    if unit.dimension != interval.dimension:
        if unit.dimension == 'percent' and interval.dimension in _PERCENTS:
            presumed.append(f'«%» понят как «{interval.unit}»')
        elif interval.dimension == 'percent' and unit.dimension in _PERCENTS:
            pass
        else:
            return U, f'другая величина: {fact.unit} — не {interval.unit}', []
    low = fact.value * unit.factor
    high = fact.value_max * unit.factor if fact.value_max is not None else low
    low, high = min(low, high), max(low, high)
    shown = f'{low} {interval.unit}' if low == high else f'{low}–{high} {interval.unit}'
    if interval.contains(low) and interval.contains(high):
        return T, f'{shown} ∈ {interval.text}', presumed
    below = interval.low is not None and (high < interval.low or (high == interval.low and not interval.low_inclusive))
    above = interval.high is not None and (low > interval.high or (low == interval.high and not interval.high_inclusive))
    if below or above:
        return F, f'{shown} ∉ {interval.text}', presumed
    return U, f'{shown} пересекает границу {interval.text}', presumed
