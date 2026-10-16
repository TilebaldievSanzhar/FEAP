"""НБКР: разбор фидов и архива, «курс на каждый день», причины отказа.

Фикстуры — настоящие ответы НБКР, снятые 16.10.2026 около 11:30 по Бишкеку.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from feap.clock import BISHKEK_TZ
from feap.refsync import nbkr

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "nbkr"
DAILY = (FIXTURES / "daily_2026-10-16.xml").read_bytes()
WEEKLY = (FIXTURES / "weekly_2026-10-10.xml").read_bytes()
ARCHIVE_EUR = (FIXTURES / "archive_eur_2026-10-08_17.html").read_bytes()

D = dt.date


def _feed(date_attr: str | None, currencies: dict[str, tuple[str, str]], *, valid_for: str | None = None) -> bytes:
    date = f' Date="{date_attr}"' if date_attr is not None else ""
    body = "".join(
        f'<Currency ISOCode="{iso}"><Nominal>{nominal}</Nominal>'
        + (f"<ValidFor>{valid_for}</ValidFor>" if valid_for else "")
        + f"<Value>{value}</Value></Currency>"
        for iso, (nominal, value) in currencies.items()
    )
    return f'<?xml version="1.0" encoding="windows-1251" ?><CurrencyRates Name="x"{date}>{body}</CurrencyRates>'.encode("cp1251")


FIVE = {"USD": ("1", "87,4500"), "EUR": ("1", "98,0839"), "CNY": ("1", "13,0689"),
        "KZT": ("1", "0,1926"), "RUB": ("1", "1,0409")}


# ── Фиды ─────────────────────────────────────────────────────────────────────

def test_parse_real_daily_feed():
    pub = nbkr.parse_feed(DAILY, "daily")
    assert pub.kind == "daily"
    assert pub.effective_from == D(2026, 10, 16)
    assert pub.rates == {
        "USD": Decimal("87.4500"), "EUR": Decimal("98.0839"), "CNY": Decimal("13.0689"),
        "KZT": Decimal("0.1926"), "RUB": Decimal("1.0409"),
    }
    assert pub.valid_days is None


def test_parse_real_weekly_feed_per_unit_rates():
    pub = nbkr.parse_feed(WEEKLY, "weekly")
    assert pub.effective_from == D(2026, 10, 10)
    assert pub.valid_days == 7
    assert len(pub.rates) == 43
    # Номинал 10/100 → курс за единицу валюты.
    assert pub.nominals["UZS"] == 10 and pub.rates["UZS"] == Decimal("0.00738")
    assert pub.nominals["IRR"] == 100 and pub.rates["IRR"] == Decimal("0.000049")
    assert pub.rates["TRY"] == Decimal("1.7723")
    assert {"AED", "VND", "GBP", "JPY"} <= set(pub.rates)
    assert not set(nbkr.DAILY_CURRENCIES) & set(pub.rates)


def test_feed_without_date_is_an_error_not_today():
    with pytest.raises(nbkr.NbkrParseError, match="дата публикации"):
        nbkr.parse_feed(_feed(None, FIVE), "daily")
    with pytest.raises(nbkr.NbkrParseError, match="дата публикации"):
        nbkr.parse_feed(_feed("2026-10-16", FIVE), "daily")


def test_daily_feed_must_have_all_five_currencies():
    partial = {k: v for k, v in FIVE.items() if k != "CNY"}
    with pytest.raises(nbkr.NbkrParseError, match="CNY"):
        nbkr.parse_feed(_feed("16.10.2026", partial), "daily")


@pytest.mark.parametrize("value", ["", "abc", "0", "-1,5", "NaN"])
def test_bad_rate_values_rejected(value):
    rates = {**FIVE, "USD": ("1", value)}
    with pytest.raises(nbkr.NbkrParseError):
        nbkr.parse_feed(_feed("16.10.2026", rates), "daily")


def test_weekly_feed_needs_single_validity_window():
    two = _feed("10.10.2026", {"TRY": ("1", "1,77")}, valid_for="7").replace(
        b"</CurrencyRates>",
        b'<Currency ISOCode="UZS"><Nominal>10</Nominal><ValidFor>14</ValidFor><Value>0,07</Value></Currency></CurrencyRates>',
    )
    with pytest.raises(nbkr.NbkrParseError, match="окно"):
        nbkr.parse_feed(two, "weekly")


def test_malformed_xml():
    with pytest.raises(nbkr.NbkrParseError):
        nbkr.parse_feed(b"<html>503 Service Unavailable</html", "daily")
    with pytest.raises(nbkr.NbkrParseError, match="корень"):
        nbkr.parse_feed(b"<html><body>maintenance</body></html>", "daily")


# ── Курс на каждый день ──────────────────────────────────────────────────────

def _days(rows):
    return sorted({r.day for r in rows})


def test_daily_rows_same_day():
    pub = nbkr.parse_feed(DAILY, "daily")
    rows, warning = nbkr.daily_rows(pub, D(2026, 10, 16))
    assert warning is None
    assert _days(rows) == [D(2026, 10, 16)]
    assert {r.currency for r in rows} == set(nbkr.DAILY_CURRENCIES)
    assert all(r.source == nbkr.SOURCE_DAILY and r.effective_from == D(2026, 10, 16) for r in rows)


def test_daily_rows_tomorrows_rate_published_ahead():
    """Вечером фид показывает курс на завтра: подтверждаем только завтра."""
    pub = nbkr.parse_feed(DAILY, "daily")  # Date=16.10
    rows, warning = nbkr.daily_rows(pub, D(2026, 10, 15))
    assert warning is None
    assert _days(rows) == [D(2026, 10, 16)]


def test_daily_rows_weekend_inference():
    """Курс с датой субботы виден до вечера понедельника — действует сб, вс, пн."""
    pub = nbkr.parse_feed(_feed("10.10.2026", FIVE), "daily")
    rows, warning = nbkr.daily_rows(pub, D(2026, 10, 12))
    assert warning is None
    assert _days(rows) == [D(2026, 10, 10), D(2026, 10, 11), D(2026, 10, 12)]
    assert all(r.effective_from == D(2026, 10, 10) for r in rows)


def test_daily_rows_stale_feed_confirms_only_its_own_day():
    pub = nbkr.parse_feed(_feed("01.10.2026", FIVE), "daily")
    rows, warning = nbkr.daily_rows(pub, D(2026, 10, 16))
    assert _days(rows) == [D(2026, 10, 1)]
    assert warning and "15 дн." in warning


def test_daily_rows_at_inference_limit():
    pub = nbkr.parse_feed(_feed("11.10.2026", FIVE), "daily")
    rows, warning = nbkr.daily_rows(pub, D(2026, 10, 16))
    assert warning is None and len(_days(rows)) == nbkr.MAX_INFER_DAYS + 1


def test_weekly_rows_cover_validity_window():
    pub = nbkr.parse_feed(WEEKLY, "weekly")
    rows, warning = nbkr.weekly_rows(pub, D(2026, 10, 16))
    assert warning is None
    assert _days(rows) == [D(2026, 10, 10) + dt.timedelta(days=i) for i in range(7)]
    assert len(rows) == 7 * 43


def test_weekly_rows_expired_window_warns():
    pub = nbkr.parse_feed(WEEKLY, "weekly")
    rows, warning = nbkr.weekly_rows(pub, D(2026, 10, 17))
    assert max(_days(rows)) == D(2026, 10, 16)
    assert warning and "16.10.2026" in warning


# ── Архив ────────────────────────────────────────────────────────────────────

def test_parse_real_archive_every_calendar_day():
    rows = nbkr.parse_archive(ARCHIVE_EUR, "EUR")
    by_day = {r.day: r.rate for r in rows}
    assert by_day[D(2026, 10, 16)] == Decimal("98.0839")
    # Курс, установленный в пятницу, действует в субботу, воскресенье и понедельник.
    assert by_day[D(2026, 10, 10)] == by_day[D(2026, 10, 11)] == by_day[D(2026, 10, 12)] == Decimal("98.1276")
    assert by_day[D(2026, 10, 13)] == Decimal("97.8828")
    # Курса на завтра в архиве до публикации нет.
    assert D(2026, 10, 17) not in by_day
    assert all(r.source == nbkr.SOURCE_ARCHIVE and r.currency == "EUR" for r in rows)


def test_archive_duplicate_day_rejected():
    html = ("<!--date-->16.10.2026<!--date--><!--value-->1,0<!--value-->"
            "<!--date-->16.10.2026<!--date--><!--value-->1,1<!--value-->").encode()
    with pytest.raises(nbkr.NbkrParseError, match="дважды"):
        nbkr.parse_archive(html, "USD")


def test_archive_url():
    url = nbkr.archive_url("https://www.nbkr.kg/index1.jsp", "USD", D(2026, 10, 8), D(2026, 10, 17))
    assert url.startswith("https://www.nbkr.kg/index1.jsp?item=1562&lang=RUS&valuta_id=15")
    assert "beg_day=08&beg_month=10&beg_year=2026" in url
    assert "end_day=17&end_month=10&end_year=2026" in url


# ── Причины отказа ───────────────────────────────────────────────────────────

TODAY = D(2026, 10, 16)
OK_AT = dt.datetime(2026, 10, 16, 3, 15, tzinfo=dt.timezone.utc)


def _reason(**kw):
    base = dict(currency="USD", day=TODAY, today=TODAY, known_currency=True,
                last_success=OK_AT, sync_error=None)
    return nbkr.missing_reason(**{**base, **kw})


def test_reason_unknown_currency():
    assert "не устанавливает" in _reason(currency="XYZ", known_currency=False)


def test_reason_far_future():
    assert "ещё не установлен" in _reason(day=D(2026, 10, 20))


def test_reason_tomorrow_before_publication():
    msg = _reason(day=D(2026, 10, 17))
    assert "ещё не опубликован" in msg and "16:00" in msg


def test_reason_source_down_shows_last_success_in_bishkek_time():
    msg = _reason(sync_error="НБКР не ответил вовремя")
    assert msg == "НБКР не ответил вовремя (последняя успешная сверка 16.10.2026 09:15) — курс не подтверждён"
    assert OK_AT.astimezone(BISHKEK_TZ).strftime("%d.%m.%Y %H:%M") in msg


def test_reason_source_down_tomorrow_is_not_doubled():
    msg = _reason(day=D(2026, 10, 17), sync_error="нет связи с НБКР (ConnectError)")
    assert msg == "нет связи с НБКР (ConnectError) — курс на завтра не проверен"


def test_reason_never_synced():
    assert _reason(last_success=None) == "нет связи с НБКР — курс не подтверждён"


def test_reason_gap_and_stale_feed_warning():
    assert "пропуск" in _reason(day=D(2026, 10, 1))
    assert _reason(source_warning="НБКР не обновлял ежедневный курс 9 дн.") == \
        "НБКР не обновлял ежедневный курс 9 дн."
