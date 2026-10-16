"""Курсы НБКР в БД: запись «курс на день», чтение на дату, догрузка по требованию.

Чтение — только точное совпадение дня (строка на каждый календарный день,
см. refsync.nbkr). Нет строки → курс не подтверждён: для сегодня/завтра
НБКР опрашивается прямо во время расчёта (если последняя сверка старше
REFDATA_FX_ON_DEMAND_AFTER_S), для прошлого дня — архив; не вышло —
FxResolution с причиной для needs_input. Старый курс не подставляется никогда.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import email.utils
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

import httpx
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from feap.clock import now_utc, today_bishkek
from feap.config import settings
from feap.db.models import ExchangeRate, RefDataset
from feap.refsync import alerts, nbkr, status

logger = logging.getLogger(__name__)

_RATE_QUANT = Decimal("0.000001")


@dataclass(frozen=True)
class FxQuote:
    """Официальный курс, действующий в день day, и откуда он взялся."""

    currency: str
    day: dt.date
    rate: Decimal
    source: str
    effective_from: dt.date | None = None
    fetched_at: dt.datetime | None = None
    published_at: dt.datetime | None = None

    def to_json(self) -> dict:
        return {
            "currency": self.currency,
            "day": self.day.isoformat(),
            "rate": format(self.rate, "f"),
            "source": self.source,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "published_at": self.published_at.isoformat() if self.published_at else None,
        }


@dataclass(frozen=True)
class FxResolution:
    quote: FxQuote | None
    issue: str | None = None


# ── Запись ───────────────────────────────────────────────────────────────────

async def upsert_day_rates(
    session: AsyncSession,
    rows: list[nbkr.DayRate],
    *,
    fetched_at: dt.datetime,
    published_at: dt.datetime | None = None,
) -> dict:
    """Записать курсы на дни. Совпадение — без изменений; расхождение с уже
    подтверждённым курсом — поправка (архив главнее фидов), она уходит в
    stats['corrected'] и в оповещения."""
    stats: dict = {"rows": len(rows), "inserted": 0, "unchanged": 0, "corrected": []}
    if not rows:
        return stats
    keys = list({(r.day, r.currency) for r in rows})
    existing: dict[tuple[dt.date, str], ExchangeRate] = {}
    for chunk_start in range(0, len(keys), 500):
        chunk = keys[chunk_start:chunk_start + 500]
        result = await session.execute(
            select(ExchangeRate).where(
                tuple_(ExchangeRate.rate_date, ExchangeRate.currency).in_(chunk)
            )
        )
        existing.update({(e.rate_date, e.currency): e for e in result.scalars().all()})

    for row in rows:
        rate = row.rate.quantize(_RATE_QUANT)
        current = existing.get((row.day, row.currency))
        if current is None:
            current = ExchangeRate(
                rate_date=row.day, currency=row.currency, rate=rate, source=row.source,
                effective_from=row.effective_from, nominal=row.nominal,
                fetched_at=fetched_at, published_at=published_at,
            )
            session.add(current)
            existing[(row.day, row.currency)] = current
            stats["inserted"] += 1
            continue
        if current.source not in nbkr.CONFIRMED_SOURCES:
            # legacy-строка (до миграции 014) — заменяем подтверждённой.
            _assign(current, row, rate, fetched_at, published_at)
            stats["inserted"] += 1
            continue
        if Decimal(current.rate) == rate:
            stats["unchanged"] += 1
            continue
        entry = {
            "day": row.day.isoformat(), "currency": row.currency,
            "before": format(Decimal(current.rate), "f"), "before_source": current.source,
            "after": format(rate, "f"), "source": row.source,
        }
        if nbkr.SOURCE_PRIORITY.get(row.source, 0) >= nbkr.SOURCE_PRIORITY.get(current.source, 0):
            _assign(current, row, rate, fetched_at, published_at)
        else:
            entry["kept"] = current.source
        stats["corrected"].append(entry)
    await session.flush()
    return stats


def _assign(
    target: ExchangeRate, row: nbkr.DayRate, rate: Decimal,
    fetched_at: dt.datetime, published_at: dt.datetime | None,
) -> None:
    target.rate = rate
    target.source = row.source
    target.effective_from = row.effective_from
    target.nominal = row.nominal
    target.fetched_at = fetched_at
    target.published_at = published_at


# ── Источники ────────────────────────────────────────────────────────────────

#: Разовые обрывы соединения с НБКР бывают: по расписанию — один повтор.
_RETRY_ERRORS = (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError)


async def _fetch(url: str, timeout: float, retries: int = 0) -> tuple[bytes, httpx.Headers]:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(retries + 1):
            try:
                resp = await client.get(url, headers={"User-Agent": "FEAP-refsync/1.0"})
                resp.raise_for_status()
                return resp.content, resp.headers
            except _RETRY_ERRORS:
                if attempt >= retries:
                    raise
                await asyncio.sleep(1.0)
    raise AssertionError("unreachable")


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "НБКР не ответил вовремя"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"НБКР ответил HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return f"нет связи с НБКР ({type(exc).__name__})"
    return str(exc) or type(exc).__name__


def _http_date(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    try:
        return email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


async def sync_feeds(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trigger: str,
    timeout: float | None = None,
    retries: int = 1,
) -> dict[str, str]:
    """Опрос daily.xml и weekly.xml → курсы на дни. {'nbkr_daily': 'ok' | 'failed: …' | 'skipped'}."""
    timeout = timeout or settings.REFDATA_HTTP_TIMEOUT_S
    started = now_utc()
    feeds = (
        ("nbkr_daily", "daily", settings.NBKR_RATES_URL),
        ("nbkr_weekly", "weekly", settings.NBKR_WEEKLY_URL),
    )
    fetched = await asyncio.gather(
        *(_fetch(url, timeout, retries) for _, _, url in feeds), return_exceptions=True,
    )
    outcome: dict[str, str] = {}
    to_send: list[alerts.Alert] = []
    for (key, kind, url), result in zip(feeds, fetched):
        async with session_factory() as session, session.begin():
            if not await status.try_lock(session, key):
                outcome[key] = "skipped"
                continue
            outcome[key] = await _store_feed(
                session, key=key, kind=kind, url=url, result=result,
                started=started, trigger=trigger, to_send=to_send,
            )
    await alerts.send(to_send)
    return outcome


async def _store_feed(
    session: AsyncSession,
    *,
    key: str,
    kind: str,
    url: str,
    result: tuple[bytes, httpx.Headers] | BaseException,
    started: dt.datetime,
    trigger: str,
    to_send: list[alerts.Alert],
) -> str:
    state = await status.get_dataset(session, key)
    was_alerted = state.alerted_at is not None and state.status == "failing"
    previous_warning = (state.meta or {}).get("warning")
    try:
        if isinstance(result, BaseException):
            raise result
        content, headers = result
        pub = nbkr.parse_feed(content, kind)
    except Exception as exc:  # noqa: BLE001 — любой сбой источника = неудачная сверка
        error = _error_text(exc)
        state = await status.record_failure(
            session, key, trigger=trigger, started_at=started, error=error, source_url=url,
        )
        if (alert := alerts.failure_alert(state, now_utc())) is not None:
            to_send.append(alert)
        return f"failed: {error}"

    observed_on = today_bishkek()
    rows, warning = (nbkr.daily_rows if kind == "daily" else nbkr.weekly_rows)(pub, observed_on)
    published_at = _http_date(headers.get("last-modified"))
    stats = await upsert_day_rates(session, rows, fetched_at=now_utc(), published_at=published_at)
    changed = stats["inserted"] > 0 or bool(stats["corrected"])
    meta = {
        "publication_date": pub.effective_from.isoformat(),
        "valid_days": pub.valid_days,
        "published_at": published_at.isoformat() if published_at else None,
        "currencies": sorted(pub.rates),
        "warning": warning,
    }
    state = await status.get_dataset(session, key)
    await status.record_success(
        session, key, trigger=trigger, started_at=started, changed=changed,
        stats={**stats, "publication_date": meta["publication_date"]},
        source_url=url, raw=content, meta=meta,
    )
    if (alert := alerts.recovery_alert(state, was_alerted)) is not None:
        to_send.append(alert)
    if warning and warning != previous_warning:
        to_send.append(alerts.info_alert(key, warning))
    if stats["corrected"]:
        to_send.append(alerts.info_alert(key, f"НБКР изменил опубликованный курс: {_corrections(stats)}"))
    return "ok"


def _corrections(stats: dict) -> str:
    parts = [
        f"{c['currency']} на {c['day']}: {c['before']} → {c['after']}"
        + (f" (оставлен {c['kept']})" if c.get("kept") else "")
        for c in stats["corrected"][:10]
    ]
    more = len(stats["corrected"]) - len(parts)
    return "; ".join(parts) + (f" и ещё {more}" if more > 0 else "")


async def reconcile_archive(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trigger: str,
    days: int = 10,
    start: dt.date | None = None,
    end: dt.date | None = None,
    currencies: tuple[str, ...] = nbkr.DAILY_CURRENCIES,
    timeout: float | None = None,
    retries: int = 1,
) -> str:
    """Сверка ежедневных курсов с архивом НБКР: дозаполнить дни, поправить расхождения."""
    timeout = timeout or settings.REFDATA_HTTP_TIMEOUT_S
    today = today_bishkek()
    end = end or today + dt.timedelta(days=1)
    start = start or today - dt.timedelta(days=days)
    started = now_utc()
    urls = {c: nbkr.archive_url(settings.NBKR_ARCHIVE_URL, c, start, end) for c in currencies}
    fetched = await asyncio.gather(
        *(_fetch(urls[c], timeout, retries) for c in currencies), return_exceptions=True,
    )
    rows: list[nbkr.DayRate] = []
    errors: list[str] = []
    for currency, result in zip(currencies, fetched):
        if isinstance(result, BaseException):
            errors.append(f"{currency}: {_error_text(result)}")
            continue
        try:
            parsed = nbkr.parse_archive(result[0], currency)
        except nbkr.NbkrParseError as exc:
            errors.append(f"{currency}: {exc}")
            continue
        if not parsed and start <= today:
            errors.append(f"{currency}: архив НБКР пуст за {start:%d.%m.%Y}–{end:%d.%m.%Y}")
        rows.extend(r for r in parsed if start <= r.day <= end)

    to_send: list[alerts.Alert] = []
    async with session_factory() as session, session.begin():
        if not await status.try_lock(session, "nbkr_archive"):
            return "skipped"
        state = await status.get_dataset(session, "nbkr_archive")
        was_alerted = state.alerted_at is not None and state.status == "failing"
        stats = await upsert_day_rates(session, rows, fetched_at=now_utc())
        stats.update({"start": start.isoformat(), "end": end.isoformat(),
                      "currencies": list(currencies), "errors": errors})
        source_url = urls[currencies[0]] if len(currencies) == 1 else settings.NBKR_ARCHIVE_URL
        if errors and not rows:
            state = await status.record_failure(
                session, "nbkr_archive", trigger=trigger, started_at=started,
                error="; ".join(errors), source_url=source_url, stats=stats,
            )
            if (alert := alerts.failure_alert(state, now_utc())) is not None:
                to_send.append(alert)
            result_text = "failed: " + "; ".join(errors)
        else:
            changed = stats["inserted"] > 0 or bool(stats["corrected"])
            await status.record_success(
                session, "nbkr_archive", trigger=trigger, started_at=started,
                stats=stats, changed=changed, source_url=source_url,
            )
            if (alert := alerts.recovery_alert(state, was_alerted)) is not None:
                to_send.append(alert)
            if stats["corrected"]:
                to_send.append(alerts.info_alert(
                    "nbkr_archive", f"архив НБКР расходится с полученными курсами: {_corrections(stats)}",
                ))
            result_text = "ok" if not errors else "ok (частично: " + "; ".join(errors) + ")"
    await alerts.send(to_send)
    return result_text


# ── Чтение ───────────────────────────────────────────────────────────────────

async def read_quote(session: AsyncSession, currency: str, day: dt.date) -> FxQuote | None:
    """Подтверждённый курс на день (точное совпадение) или None."""
    currency = currency.upper()
    if currency == "KGS":
        return FxQuote("KGS", day, Decimal(1), "kgs")
    result = await session.execute(
        select(ExchangeRate).where(
            ExchangeRate.currency == currency,
            ExchangeRate.rate_date == day,
            ExchangeRate.source.in_(nbkr.CONFIRMED_SOURCES),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return FxQuote(
        currency=currency, day=day, rate=Decimal(row.rate), source=row.source,
        effective_from=row.effective_from, fetched_at=row.fetched_at,
        published_at=row.published_at,
    )


_sync_lock = asyncio.Lock()
_ARCHIVE_RETRY_S = 600.0
_archive_attempts: dict[tuple[str, dt.date], float] = {}


def _is_stale(state: RefDataset | None) -> bool:
    if state is None or state.last_success_at is None:
        return True
    age = (now_utc() - state.last_success_at).total_seconds()
    return age > settings.REFDATA_FX_ON_DEMAND_AFTER_S


async def _sync_on_demand(
    session_factory: async_sessionmaker[AsyncSession], key: str,
) -> str | None:
    """Опрос фидов во время расчёта (один на процесс). Возвращает ошибку или None."""
    async with _sync_lock:
        async with session_factory() as probe:
            if not _is_stale(await probe.get(RefDataset, key)):
                return None  # пока ждали замок, курс уже обновил другой запрос
        try:
            outcome = await sync_feeds(
                session_factory, trigger="on_demand",
                timeout=settings.REFDATA_ON_DEMAND_TIMEOUT_S, retries=0,
            )
        except Exception as exc:  # noqa: BLE001 — расчёт не падает из-за источника
            logger.exception("on-demand NBKR sync failed")
            return _error_text(exc)
    result = outcome.get(key, "")
    return result.removeprefix("failed: ") if result.startswith("failed") else None


async def _archive_on_demand(
    session_factory: async_sessionmaker[AsyncSession], currency: str, day: dt.date,
) -> None:
    attempt_key = (currency, day)
    last = _archive_attempts.get(attempt_key)
    if last is not None and time.monotonic() - last < _ARCHIVE_RETRY_S:
        return
    _archive_attempts[attempt_key] = time.monotonic()
    try:
        await reconcile_archive(
            session_factory, trigger="on_demand", start=day, end=day, currencies=(currency,),
            timeout=settings.REFDATA_ON_DEMAND_TIMEOUT_S + 2, retries=0,
        )
    except Exception:  # noqa: BLE001
        logger.exception("on-demand NBKR archive fetch failed")


async def resolve_fx(
    session: AsyncSession,
    currency: str,
    day: dt.date,
    *,
    allow_sync: bool = True,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> FxResolution:
    """Курс на день или причина, почему его нет (для needs_input)."""
    currency = currency.upper()
    quote = await read_quote(session, currency, day)
    if quote is not None:
        return FxResolution(quote)

    today = today_bishkek()
    is_daily = currency in nbkr.DAILY_CURRENCIES
    key = "nbkr_daily" if is_daily else "nbkr_weekly"
    states = await status.load_datasets(session)
    state = states.get(key)
    weekly_known = set(((states.get("nbkr_weekly") or RefDataset(meta={})).meta or {}).get("currencies") or [])
    known = is_daily or not weekly_known or currency in weekly_known

    sync_error: str | None = None
    if allow_sync and known:
        factory = session_factory or _default_factory()
        if today <= day <= today + dt.timedelta(days=1) and _is_stale(state):
            sync_error = await _sync_on_demand(factory, key)
        elif day < today and currency in nbkr.ARCHIVE_VALUTA_IDS:
            await _archive_on_demand(factory, currency, day)
        quote = await read_quote(session, currency, day)
        if quote is not None:
            return FxResolution(quote)
        # Состояние обновила другая сессия; expire_all нельзя — в async-сессии
        # запроса это сломало бы ленивую загрузку уже прочитанных объектов.
        state = (await session.execute(
            select(RefDataset).where(RefDataset.dataset == key)
            .execution_options(populate_existing=True)
        )).scalar_one_or_none()

    meta = (state.meta or {}) if state is not None else {}
    if state is not None and state.status == "failing" and not sync_error:
        sync_error = state.last_error
    issue = nbkr.missing_reason(
        currency=currency, day=day, today=today, known_currency=known,
        last_success=state.last_success_at if state is not None else None,
        sync_error=sync_error, source_warning=meta.get("warning"),
    )
    return FxResolution(None, issue)


def _default_factory() -> async_sessionmaker[AsyncSession]:
    from feap.db.engine import async_session

    return async_session
