"""Официальные курсы НБКР: разбор фидов и архива, «курс на каждый день».

Источники (проверено 16.10.2026):
- XML/daily.xml — последняя публикация 5 валют. Date — день, с которого курс
  действует; курс на завтра НБКР выкладывает накануне около 16:00, после этого
  файл показывает уже завтрашний курс.
- XML/weekly.xml — ещё ~43 валюты с окном действия Date + ValidFor дней.
- Архив index1.jsp?item=1562 — курс на каждый календарный день (включая
  выходные: курс, установленный в пятницу, действует в субботу–понедельник).

Модуль чистый (без БД и сети): из публикаций строятся строки DayRate —
курс, действующий в конкретный день. Чтение курса — только точное совпадение
дня (refsync.fx), «последний не позже даты» не используется.
"""

from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from feap.clock import BISHKEK_TZ

#: Валюты ежедневного фида (с 01.09.2024 — включая юань).
DAILY_CURRENCIES: tuple[str, ...] = ("USD", "EUR", "CNY", "KZT", "RUB")

#: Коды валют в форме архива НБКР (valuta_id) — только ежедневные валюты:
#: у еженедельных в архиве несколько записей с разными номиналами и эпохами.
ARCHIVE_VALUTA_IDS: dict[str, int] = {"USD": 15, "EUR": 20, "CNY": 24, "KZT": 40, "RUB": 44}

#: Дольше НБКР курс не «замораживает» (выходные + праздники). Публикация
#: старше — признак сбоя фида: на дни после неё курс не выводим.
MAX_INFER_DAYS = 5

SOURCE_DAILY = "nbkr_daily"
SOURCE_WEEKLY = "nbkr_weekly"
SOURCE_ARCHIVE = "nbkr_archive"
#: Строки до миграции 014: курс на дату мог быть пропущен — не используются.
SOURCE_LEGACY = "legacy"
CONFIRMED_SOURCES: frozenset[str] = frozenset({SOURCE_DAILY, SOURCE_WEEKLY, SOURCE_ARCHIVE})

#: При расхождении архив главнее фидов; при равенстве — свежее наблюдение.
SOURCE_PRIORITY: dict[str, int] = {SOURCE_ARCHIVE: 2, SOURCE_DAILY: 1, SOURCE_WEEKLY: 1}


class NbkrParseError(ValueError):
    """Фид или архив НБКР не удалось разобрать — данные не используются."""


@dataclass(frozen=True)
class Publication:
    """Одна публикация фида НБКР."""

    kind: str                          # daily | weekly
    effective_from: dt.date            # атрибут Date: с этого дня курс действует
    rates: dict[str, Decimal]          # сом за 1 единицу валюты
    nominals: dict[str, int] = field(default_factory=dict)
    valid_days: int | None = None      # weekly: ValidFor


@dataclass(frozen=True)
class DayRate:
    """Курс, действующий в конкретный календарный день."""

    day: dt.date
    currency: str
    rate: Decimal
    source: str
    effective_from: dt.date
    nominal: int | None = None


def _decimal(raw: str | None) -> Decimal:
    try:
        value = Decimal((raw or "").replace(" ", "").replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise NbkrParseError(f"не число: {raw!r}") from exc
    if not value.is_finite() or value <= 0:
        raise NbkrParseError(f"некорректный курс: {raw!r}")
    return value


def _ru_date(raw: str | None) -> dt.date:
    try:
        return dt.datetime.strptime((raw or "").strip(), "%d.%m.%Y").date()
    except ValueError as exc:
        raise NbkrParseError(f"дата публикации не разобрана: {raw!r}") from exc


def parse_feed(content: bytes, kind: str) -> Publication:
    """daily.xml / weekly.xml → Publication. Без даты публикации — ошибка, не «сегодня»."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise NbkrParseError(f"XML НБКР не разобран: {exc}") from exc
    if root.tag != "CurrencyRates":
        raise NbkrParseError(f"неожиданный корень XML: {root.tag}")
    effective_from = _ru_date(root.attrib.get("Date"))

    rates: dict[str, Decimal] = {}
    nominals: dict[str, int] = {}
    valid_days: set[int] = set()
    for cur in root.iter("Currency"):
        iso = (cur.attrib.get("ISOCode") or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", iso):
            continue
        value = _decimal(cur.findtext("Value"))
        nominal_raw = (cur.findtext("Nominal") or "1").strip()
        try:
            nominal = int(Decimal(nominal_raw.replace(",", ".")))
        except InvalidOperation as exc:
            raise NbkrParseError(f"номинал {iso} не разобран: {nominal_raw!r}") from exc
        if nominal <= 0:
            raise NbkrParseError(f"номинал {iso} ≤ 0")
        rates[iso] = value / nominal
        nominals[iso] = nominal
        valid_for = cur.findtext("ValidFor")
        if valid_for:
            try:
                valid_days.add(int(valid_for.strip()))
            except ValueError as exc:
                raise NbkrParseError(f"ValidFor {iso} не разобран: {valid_for!r}") from exc

    if not rates:
        raise NbkrParseError("в публикации нет ни одного курса")
    if kind == "daily":
        missing = [c for c in DAILY_CURRENCIES if c not in rates]
        if missing:
            raise NbkrParseError(f"в ежедневном курсе нет валют: {', '.join(missing)}")
        return Publication("daily", effective_from, rates, nominals)
    if len(valid_days) != 1:
        raise NbkrParseError(f"окно действия еженедельных курсов не однозначно: {sorted(valid_days)}")
    days = valid_days.pop()
    if not 1 <= days <= 31:
        raise NbkrParseError(f"неправдоподобное окно действия: {days} дн.")
    return Publication("weekly", effective_from, rates, nominals, valid_days=days)


def daily_rows(pub: Publication, observed_on: dt.date) -> tuple[list[DayRate], str | None]:
    """Строки из наблюдения daily.xml в день observed_on (по Бишкеку).

    Фид показывает последнюю публикацию P с датой E, значит P действует на
    каждый день из [E, max(E, observed_on)]: курс с датой ≤ сегодня НБКР
    задним числом не публикует. Если E старше observed_on больше чем на
    MAX_INFER_DAYS — фид, вероятно, сломан: подтверждаем только сам день E.
    """
    lag = (observed_on - pub.effective_from).days
    warning = None
    if lag > MAX_INFER_DAYS:
        last = pub.effective_from
        warning = (f"НБКР не обновлял ежедневный курс {lag} дн. (последний — на "
                   f"{pub.effective_from:%d.%m.%Y}); курс на дни после него не подтверждён")
    else:
        last = max(pub.effective_from, observed_on)
    rows = [
        DayRate(day, currency, rate, SOURCE_DAILY, pub.effective_from, pub.nominals.get(currency))
        for day in _days(pub.effective_from, last)
        for currency, rate in sorted(pub.rates.items())
    ]
    return rows, warning


def weekly_rows(pub: Publication, observed_on: dt.date) -> tuple[list[DayRate], str | None]:
    """Строки на окно действия еженедельной публикации [Date, Date + ValidFor − 1]."""
    assert pub.valid_days is not None
    last = pub.effective_from + dt.timedelta(days=pub.valid_days - 1)
    warning = None
    if last < observed_on:
        warning = (f"окно еженедельных курсов НБКР закончилось {last:%d.%m.%Y}, новой "
                   "публикации нет — курсы этих валют на сегодня не подтверждены")
    rows = [
        DayRate(day, currency, rate, SOURCE_WEEKLY, pub.effective_from, pub.nominals.get(currency))
        for day in _days(pub.effective_from, last)
        for currency, rate in sorted(pub.rates.items())
    ]
    return rows, warning


_ARCHIVE_ROW = re.compile(
    r"<!--date-->\s*(\d{2}\.\d{2}\.\d{4})\s*<!--date-->.*?<!--value-->\s*([\d\s,. ]+?)\s*<!--value-->",
    re.S,
)


def parse_archive(content: bytes, currency: str) -> list[DayRate]:
    """Страница архива НБКР → курс на каждый день диапазона (номинал 1 у ежедневных валют)."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("cp1251", errors="replace")
    rows: list[DayRate] = []
    seen: set[dt.date] = set()
    for raw_day, raw_value in _ARCHIVE_ROW.findall(text):
        day = _ru_date(raw_day)
        if day in seen:
            raise NbkrParseError(f"в архиве дважды дата {raw_day}")
        seen.add(day)
        rows.append(DayRate(day, currency, _decimal(raw_value), SOURCE_ARCHIVE, day, 1))
    return rows


def archive_url(base: str, currency: str, start: dt.date, end: dt.date) -> str:
    params = {
        "item": "1562", "lang": "RUS", "valuta_id": ARCHIVE_VALUTA_IDS[currency],
        "beg_day": f"{start.day:02d}", "beg_month": f"{start.month:02d}", "beg_year": start.year,
        "end_day": f"{end.day:02d}", "end_month": f"{end.month:02d}", "end_year": end.year,
    }
    return f"{base}?{urlencode(params)}"


def _days(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


# ── Почему курса на дату нет ─────────────────────────────────────────────────

def missing_reason(
    *,
    currency: str,
    day: dt.date,
    today: dt.date,
    known_currency: bool,
    last_success: dt.datetime | None,
    sync_error: str | None,
    source_warning: str | None = None,
) -> str:
    """Человеческое объяснение для needs_input: почему курс на day не подтверждён."""
    if not known_currency:
        return f"НБКР не устанавливает официальный курс {currency}"
    if day > today + dt.timedelta(days=1):
        return (f"курс на {day:%d.%m.%Y} ещё не установлен — НБКР устанавливает курс "
                "накануне дня действия")
    if day > today:
        if sync_error:
            return f"{sync_error} — курс на завтра не проверен"
        return (f"курс на {day:%d.%m.%Y} ещё не опубликован — НБКР публикует курс "
                "на следующий день около 16:00 по Бишкеку")
    if sync_error or last_success is None:
        since = (f" (последняя успешная сверка {last_success.astimezone(BISHKEK_TZ):%d.%m.%Y %H:%M})"
                 if last_success else "")
        return f"{sync_error or 'нет связи с НБКР'}{since} — курс не подтверждён"
    if source_warning and day == today:
        return source_warning
    return f"курс на {day:%d.%m.%Y} не подтверждён: пропуск в данных НБКР"
