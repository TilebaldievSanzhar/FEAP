"""Ежедневная сверка реестра мер защиты рынка ЕЭК с trade_remedies.

Реестр (remedies.eaeunion.org) отдаёт структурные поля (коды, страны, даты,
статус) и текст «формы меры», из которого ставки разбирает
tariff.remedies.record_to_seed. Поэтому:

- структурные изменения применяются сразу (ref_changes.status='applied');
- новая мера или изменение ставок применяются сразу, но с needs_review=true:
  до подтверждения человеком декларант вводит ставку меры вручную — тот же
  механизм, что для неразобранных ставок (ref_changes.status='pending');
- мера пропала из реестра → данные не трогаем, 'pending' + оповещение;
- ответ подозрительно мал (<80% действующих мер) → запуск failed, база не меняется.

diff_measures — чистая функция (тесты без БД).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from feap.clock import now_utc, today_bishkek
from feap.config import settings
from feap.db.models import RefChange, TradeRemedy
from feap.refsync import alerts, status
from feap.tariff.remedies import record_to_seed

logger = logging.getLogger(__name__)

DATASET = "eaeu_remedies"

#: Поля, изменение которых значимо для расчёта и аудита.
MEANINGFUL: tuple[str, ...] = (
    "kind", "title", "product_description", "countries", "codes", "rates",
    "status", "valid_from", "valid_to",
)
#: Ставки разобраны из текста — их изменение подтверждает человек.
REVIEW_FIELDS: frozenset[str] = frozenset({"rates"})
#: Обновляются молча (не влияют на суммы).
QUIET: tuple[str, ...] = ("notes", "legal_basis", "source_url", "source_modified")
#: Ответ меньше этой доли действующих мер — сбой реестра, а не отмена мер.
MIN_SHARE = 0.8


@dataclass
class MeasureChange:
    measure_no: str
    change: str                      # added | updated | missing
    fields: list[str] = field(default_factory=list)
    before: dict | None = None
    after: dict | None = None
    needs_review: bool = False

    @property
    def effective_from(self) -> dt.date | None:
        raw = (self.after or {}).get("valid_from")
        return _as_date(raw)


def _as_date(raw) -> dt.date | None:
    if raw is None or isinstance(raw, dt.date):
        return raw
    try:
        return dt.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _pct(raw) -> str:
    try:
        return format(Decimal(str(raw)).normalize(), "f")
    except (InvalidOperation, ValueError):
        return str(raw)


def _norm(name: str, value):
    """Сравнимое представление поля: порядок списков и формат чисел/дат не важны."""
    if name in ("countries", "codes"):
        return sorted(str(v) for v in (value or []))
    if name == "rates":
        return sorted(
            json.dumps({
                "rate_pct": _pct(r.get("rate_pct")),
                "country": r.get("country"),
                "producers": sorted(r.get("producers") or []),
                "variant": r.get("variant"),
            }, ensure_ascii=False, sort_keys=True)
            for r in (value or [])
        )
    if name in ("valid_from", "valid_to"):
        d = _as_date(value)
        return d.isoformat() if d else None
    if isinstance(value, str):
        return " ".join(value.split()) or None
    return value


def _jsonable(name: str, value):
    if name in ("valid_from", "valid_to"):
        d = _as_date(value)
        return d.isoformat() if d else None
    return value


def diff_measures(current: dict[str, dict], incoming: dict[str, dict]) -> list[MeasureChange]:
    """current — строки trade_remedies, incoming — record_to_seed по реестру (оба по measure_no)."""
    changes: list[MeasureChange] = []
    for no, new in sorted(incoming.items()):
        old = current.get(no)
        if old is None:
            if new.get("status") == "expired":
                continue  # историческая мера — в базу не тянем
            changes.append(MeasureChange(
                no, "added", list(MEANINGFUL), None,
                {**{f: _jsonable(f, new.get(f)) for f in MEANINGFUL},
                 "rate_text": new.get("rate_text"), "parsed_needs_review": bool(new.get("needs_review"))},
                needs_review=True,
            ))
            continue
        fields = [f for f in MEANINGFUL if _norm(f, old.get(f)) != _norm(f, new.get(f))]
        if fields:
            after = {f: _jsonable(f, new.get(f)) for f in fields}
            review = bool(REVIEW_FIELDS & set(fields))
            if review:
                after["rate_text"] = new.get("rate_text")
                after["parsed_needs_review"] = bool(new.get("needs_review"))
            changes.append(MeasureChange(
                no, "updated", fields, {f: _jsonable(f, old.get(f)) for f in fields}, after,
                needs_review=review,
            ))
    for no, old in sorted(current.items()):
        if no not in incoming and old.get("status") != "expired":
            changes.append(MeasureChange(
                no, "missing", [], {"status": old.get("status"), "title": old.get("title")}, None,
            ))
    return changes


def _row_dict(r: TradeRemedy) -> dict:
    return {
        "measure_no": r.measure_no, "kind": r.kind, "title": r.title,
        "product_description": r.product_description, "countries": list(r.countries or []),
        "codes": list(r.codes or []), "rates": list(r.rates or []), "status": r.status,
        "valid_from": r.valid_from, "valid_to": r.valid_to, "needs_review": r.needs_review,
    }


async def fetch_registry(timeout: float | None = None, retries: int = 1) -> tuple[list[dict], bytes]:
    async with httpx.AsyncClient(timeout=timeout or 60.0) as client:
        for attempt in range(retries + 1):
            try:
                resp = await client.post(
                    settings.EAEU_REMEDIES_URL, content="{}",
                    headers={
                        "Content-Type": "text/plain",
                        "Origin": "https://remedies.eaeunion.org",
                        "Referer": "https://remedies.eaeunion.org/dimd/ru/security/common",
                        "User-Agent": "FEAP-refsync/1.0",
                    },
                )
                resp.raise_for_status()
                return list(resp.json().get("result") or []), resp.content
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError):
                if attempt >= retries:
                    raise
                await asyncio.sleep(2.0)
    raise AssertionError("unreachable")


def _apply(session: AsyncSession, rows: dict[str, TradeRemedy], incoming: dict[str, dict],
           change: MeasureChange) -> None:
    new = incoming.get(change.measure_no)
    if change.change == "missing" or new is None:
        return
    row = rows.get(change.measure_no)
    if row is None:
        row = TradeRemedy(measure_no=change.measure_no)
        session.add(row)
        rows[change.measure_no] = row
    for f in MEANINGFUL + QUIET:
        value = new.get(f)
        if f in ("valid_from", "valid_to"):
            value = _as_date(value)
        setattr(row, f, value)
    row.countries = list(new.get("countries") or [])
    row.codes = list(new.get("codes") or [])
    row.rates = list(new.get("rates") or [])
    # Ставки из текста ещё не проверены человеком → ставка вводится вручную.
    row.needs_review = True if change.needs_review else bool(row.needs_review)


async def sync_remedies(
    session_factory: async_sessionmaker[AsyncSession], *, trigger: str,
) -> str:
    started = now_utc()
    to_send: list[alerts.Alert] = []
    try:
        records, raw = await fetch_registry(settings.REFDATA_HTTP_TIMEOUT_S * 2)
        fetch_error = None
    except Exception as exc:  # noqa: BLE001 — любой сбой источника = неудачная сверка
        records, raw, fetch_error = [], b"", f"реестр ЕЭК недоступен: {type(exc).__name__}"
        if isinstance(exc, httpx.HTTPStatusError):
            fetch_error = f"реестр ЕЭК ответил HTTP {exc.response.status_code}"

    today = today_bishkek()
    async with session_factory() as session, session.begin():
        if not await status.try_lock(session, DATASET):
            return "skipped"
        state = await status.get_dataset(session, DATASET)
        was_alerted = state.alerted_at is not None and state.status == "failing"
        rows = {r.measure_no: r for r in (await session.execute(select(TradeRemedy))).scalars().all()}
        current = {no: _row_dict(r) for no, r in rows.items()}

        error = fetch_error
        incoming: dict[str, dict] = {}
        if error is None:
            try:
                for rec in records:
                    seed = record_to_seed(rec, today=today)
                    if seed.get("measure_no"):
                        incoming[seed["measure_no"]] = seed
            except Exception as exc:  # noqa: BLE001
                error = f"ответ реестра не разобран: {exc}"
        active_now = sum(1 for m in current.values() if m["status"] != "expired")
        active_in = sum(1 for m in incoming.values() if m["status"] != "expired")
        if error is None and (not incoming or active_in < MIN_SHARE * active_now):
            error = (f"реестр ЕЭК вернул {active_in} действующих мер при {active_now} в базе — "
                     "похоже на сбой источника, база не изменена")

        if error is not None:
            state = await status.record_failure(
                session, DATASET, trigger=trigger, started_at=started, error=error,
                source_url=settings.EAEU_REMEDIES_URL,
            )
            if (alert := alerts.failure_alert(state, now_utc())) is not None:
                to_send.append(alert)
            result = f"failed: {error}"
        else:
            changes = diff_measures(current, incoming)
            for change in changes:
                _apply(session, rows, incoming, change)
            # Молчаливые поля (примечания, ссылки) — без записи в ref_changes.
            for no, new in incoming.items():
                if no in rows and no in current:
                    for f in QUIET:
                        if getattr(rows[no], f) != new.get(f):
                            setattr(rows[no], f, new.get(f))
            stats = {
                "registry_total": len(records), "active": active_in,
                "added": sum(c.change == "added" for c in changes),
                "updated": sum(c.change == "updated" for c in changes),
                "missing": sum(c.change == "missing" for c in changes),
                "needs_review": sum(c.needs_review for c in changes),
            }
            run = await status.record_success(
                session, DATASET, trigger=trigger, started_at=started, stats=stats,
                changed=bool(changes), source_url=settings.EAEU_REMEDIES_URL, raw=raw,
            )
            for change in changes:
                session.add(RefChange(
                    dataset=DATASET, key=change.measure_no, change=change.change,
                    fields=change.fields, before=change.before, after=change.after,
                    effective_from=change.effective_from,
                    status="pending" if (change.needs_review or change.change == "missing") else "applied",
                    run_id=run.id,
                ))
            if (alert := alerts.recovery_alert(state, was_alerted)) is not None:
                to_send.append(alert)
            if changes:
                to_send.append(alerts.info_alert(DATASET, _summary(changes)))
            result = "ok"
    await alerts.send(to_send)
    return result


def _summary(changes: list[MeasureChange]) -> str:
    parts = []
    for c in changes[:12]:
        if c.change == "added":
            parts.append(f"новая мера {c.measure_no}")
        elif c.change == "missing":
            parts.append(f"{c.measure_no} пропала из реестра")
        else:
            parts.append(f"{c.measure_no}: {', '.join(c.fields)}")
    more = len(changes) - len(parts)
    review = sum(c.needs_review or c.change == "missing" for c in changes)
    text = "изменения в реестре — " + "; ".join(parts) + (f" и ещё {more}" if more > 0 else "")
    if review:
        text += f". Ждут подтверждения: {review} (python -m feap.refsync changes --pending)"
    return text


# ── Подтверждение человеком ──────────────────────────────────────────────────

async def decide(session: AsyncSession, change: RefChange, *, approve: bool, by: str,
                 note: str | None = None) -> str:
    """approve: ставки меры проверены по тексту решения (needs_review снимается, если
    разбор был уверенным); мера пропала — помечается истёкшей. reject: ставки
    разобраны неверно — остаётся ручной ввод; пропавшая мера остаётся действующей."""
    if change.status != "pending":
        raise ValueError(f"изменение {change.id} уже {change.status}")
    row = (await session.execute(
        select(TradeRemedy).where(TradeRemedy.measure_no == change.key)
    )).scalar_one_or_none()
    outcome = ""
    if row is not None:
        if change.change == "missing":
            if approve:
                row.status = "expired"
                outcome = "мера помечена истёкшей"
            else:
                outcome = "мера оставлена действующей"
        elif approve:
            row.needs_review = bool((change.after or {}).get("parsed_needs_review"))
            outcome = ("ставки подтверждены" if not row.needs_review
                       else "подтверждено; ставки не разобраны — остаётся ручной ввод")
        else:
            row.needs_review = True
            outcome = "отклонено: ставка меры вводится вручную"
    change.status = "approved" if approve else "rejected"
    change.decided_at = now_utc()
    change.decided_by = by
    change.note = note
    await session.flush()
    return outcome
