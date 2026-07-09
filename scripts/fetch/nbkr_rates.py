"""Загрузка официальных курсов НБКР в exchange_rates.

Источник: ежедневный XML НБКР (settings.NBKR_RATES_URL, по умолчанию
https://www.nbkr.kg/XML/daily.xml). Формат:

    <CurrencyRates Name="Official Exchange Rates" Date="05.07.2026">
      <Currency ISOCode="USD"><Nominal>1</Nominal><Value>87,4500</Value></Currency>
      ...
    </CurrencyRates>

Идемпотентен: ON CONFLICT (rate_date, currency) DO UPDATE.
Запуск: `python scripts/fetch/nbkr_rates.py` (ежедневный cron / вручную).
"""

from __future__ import annotations

import datetime as dt
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from feap.config import settings  # noqa: E402
from feap.db.models import ExchangeRate  # noqa: E402

#: Валюты, нужные ДТ-ядру (графа 23 + специфические ставки EUR/USD).
WANTED = {"USD", "EUR", "CNY", "RUB", "KZT", "TRY", "UZS"}


def fetch_rates(url: str) -> tuple[dt.date, dict[str, Decimal]]:
    resp = httpx.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    date_attr = root.attrib.get("Date", "")
    try:
        rate_date = dt.datetime.strptime(date_attr, "%d.%m.%Y").date()
    except ValueError:
        rate_date = dt.date.today()

    rates: dict[str, Decimal] = {}
    for cur in root.iter("Currency"):
        iso = cur.attrib.get("ISOCode", "").upper()
        if iso not in WANTED:
            continue
        try:
            value = Decimal((cur.findtext("Value") or "").replace(",", ".").strip())
            nominal = Decimal((cur.findtext("Nominal") or "1").replace(",", ".").strip())
        except InvalidOperation:
            continue
        if nominal > 0:
            rates[iso] = value / nominal
    return rate_date, rates


def upsert_rates(rate_date: dt.date, rates: dict[str, Decimal]) -> int:
    engine = create_engine(settings.database_url_sync)
    with engine.begin() as conn:
        for currency, rate in rates.items():
            stmt = insert(ExchangeRate.__table__).values(
                rate_date=rate_date, currency=currency, rate=rate, source="NBKR",
            ).on_conflict_do_update(
                constraint="uq_exchange_rate_day_currency",
                set_={"rate": rate},
            )
            conn.execute(stmt)
    return len(rates)


def main() -> None:
    url = settings.NBKR_RATES_URL
    print(f"Fetching NBKR rates: {url}")
    rate_date, rates = fetch_rates(url)
    if not rates:
        print("ERROR: no rates parsed", file=sys.stderr)
        sys.exit(1)
    n = upsert_rates(rate_date, rates)
    printable = ", ".join(f"{c}={r}" for c, r in sorted(rates.items()))
    print(f"OK: {n} currencies for {rate_date.isoformat()}: {printable}")


if __name__ == "__main__":
    main()
