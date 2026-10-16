"""Ежедневная сверка ЕТТ со сводной редакцией на сайте ЕЭК.

Страница ЕТТ ЕЭК перечисляет PDF групп (в имени — дата редакции) и
«Примечания к ЕТТ». Скачиваются только группы, у которых сменилось имя файла
(первый запуск — все 96), разбираются (tariff.ett) и заменяют строки группы
в ett_rates; примечания — целиком.

Первая загрузка сравнивается со снимком TWS (tnved_tree.duty_rate) — это и
есть разовая сверка «что изменилось с февраля»; дальше — редакция с
редакцией. Изменения ставок на сегодня, новые и исключённые коды пишутся в
ref_changes (применены — источник официальный) и уходят в оповещения.

Санитарные проверки: группа, разобранная меньше чем на 90% от прежнего числа
листьев, не применяется (сбой разбора, а не отмена кодов); примечаний меньше
90% от прежнего — запуск failed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

import httpx
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from feap.clock import now_utc, today_bishkek
from feap.config import settings
from feap.db.models import EttNoteRow, EttRate, RefChange, RefDataset, TnvedNode
from feap.refsync import alerts, status
from feap.tariff import ett

logger = logging.getLogger(__name__)

DATASET = "ett_rates"
MIN_GROUPS = 90
MIN_SHARE = 0.9
_DOWNLOADS = 4
_HEADERS = {"User-Agent": "FEAP-refsync/1.0"}


def _pdf_text(pdf: bytes) -> str:
    import pymupdf  # noqa: PLC0415

    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def _parse_downloads(
    groups: dict[str, bytes], notes_pdf: bytes | None,
) -> tuple[dict[str, dict[str, ett.EttRow]], dict[str, str], dict[str, ett.EttNote] | None]:
    """Разбор в отдельном потоке (PyMuPDF — CPU): группы, ошибки групп, примечания."""
    parsed: dict[str, dict[str, ett.EttRow]] = {}
    errors: dict[str, str] = {}
    for group, pdf in groups.items():
        try:
            parsed[group] = ett.parse_group(ett.extract_spans(pdf), group)
        except Exception as exc:  # noqa: BLE001 — сбой одной группы не валит остальные
            errors[group] = f"{type(exc).__name__}: {exc}"[:300]
    notes = ett.parse_notes(_pdf_text(notes_pdf)) if notes_pdf is not None else None
    return parsed, errors, notes


async def _download(client: httpx.AsyncClient, url: str) -> bytes:
    resp = await client.get(url)
    resp.raise_for_status()
    if not resp.content.startswith(b"%PDF"):
        raise ett.EttParseError(f"не PDF: {url}")
    return resp.content


def _stored(rows: list[EttRate]) -> dict[str, ett.StoredRow]:
    return {r.code: ett.StoredRow(r.base_rate, tuple(r.notes or ()), r.version) for r in rows}


async def sync_ett(
    session_factory: async_sessionmaker[AsyncSession], *, trigger: str, force: bool = False,
) -> str:
    """Сверка ЕТТ; одновременно — только в одном процессе (сессионный advisory lock на
    всё время скачивания: два worker'а при старте не качают 96 PDF дважды)."""
    async with session_factory() as guard_session:
        got = (await guard_session.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": "refsync:ett_rates:job"},
        )).scalar()
        if not got:
            return "skipped"
        try:
            return await _sync_ett(session_factory, trigger=trigger, force=force)
        finally:
            await guard_session.execute(
                text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": "refsync:ett_rates:job"},
            )


async def _sync_ett(
    session_factory: async_sessionmaker[AsyncSession], *, trigger: str, force: bool,
) -> str:
    started = now_utc()
    page_url = settings.EEC_ETT_PAGE_URL
    to_send: list[alerts.Alert] = []

    async with session_factory() as session:
        state = await session.get(RefDataset, DATASET)
        meta = dict(state.meta or {}) if state is not None else {}
    known: dict[str, str] = meta.get("groups") or {}

    error: str | None = None
    page: ett.EttPage | None = None
    downloads: dict[str, bytes] = {}
    notes_pdf: bytes | None = None
    try:
        async with httpx.AsyncClient(
            timeout=settings.REFDATA_HTTP_TIMEOUT_S * 2, follow_redirects=True, headers=_HEADERS,
        ) as client:
            resp = await client.get(page_url)
            resp.raise_for_status()
            page = ett.parse_page(resp.text, page_url)
            if len(page.groups) < MIN_GROUPS or page.notes is None:
                raise ett.EttParseError(
                    f"на странице ЕТТ найдено {len(page.groups)} групп и "
                    f"{'нет' if page.notes is None else 'есть'} примечаний — страница изменилась?"
                )
            changed = sorted(g for g, f in page.groups.items() if force or known.get(g) != f.filename)
            semaphore = asyncio.Semaphore(_DOWNLOADS)

            async def fetch(group: str) -> tuple[str, bytes]:
                async with semaphore:
                    return group, await _download(client, page.groups[group].url)

            for group, pdf in await asyncio.gather(*(fetch(g) for g in changed)):
                downloads[group] = pdf
            if force or meta.get("notes") != page.notes.filename:
                notes_pdf = await _download(client, page.notes.url)
    except Exception as exc:  # noqa: BLE001 — любой сбой источника = неудачная сверка
        error = (f"сайт ЕЭК ответил HTTP {exc.response.status_code}"
                 if isinstance(exc, httpx.HTTPStatusError) else f"ЕТТ ЕЭК: {type(exc).__name__}: {exc}")[:500]

    parsed: dict[str, dict[str, ett.EttRow]] = {}
    group_errors: dict[str, str] = {}
    new_notes: dict[str, ett.EttNote] | None = None
    if error is None and (downloads or notes_pdf is not None):
        try:
            parsed, group_errors, new_notes = await asyncio.to_thread(_parse_downloads, downloads, notes_pdf)
        except Exception as exc:  # noqa: BLE001
            error = f"разбор ЕТТ не удался: {type(exc).__name__}: {exc}"[:500]

    today = today_bishkek()
    async with session_factory() as session, session.begin():
        if not await status.try_lock(session, DATASET):
            return "skipped"
        state = await status.get_dataset(session, DATASET)
        was_alerted = state.alerted_at is not None and state.status == "failing"

        old_note_rows = (await session.execute(select(EttNoteRow))).scalars().all()
        old_notes = {n.note_id: ett.note_from_json(n.note_id, n.kind, n.periods, n.text) for n in old_note_rows}
        if error is None and new_notes is not None and len(new_notes) < MIN_SHARE * len(old_notes):
            error = (f"в «Примечаниях к ЕТТ» разобрано {len(new_notes)} примечаний при "
                     f"{len(old_notes)} в базе — данные не изменены")

        if error is not None or page is None:
            state = await status.record_failure(
                session, DATASET, trigger=trigger, started_at=started,
                error=error or "страница ЕТТ не получена", source_url=page_url,
            )
            if (alert := alerts.failure_alert(state, now_utc())) is not None:
                to_send.append(alert)
            result = f"failed: {error}"
        else:
            notes_after = new_notes if new_notes is not None else old_notes
            applied_groups: dict[str, ett.EttFile] = {}
            old_all: dict[str, ett.StoredRow] = {}
            new_all: dict[str, ett.StoredRow] = {}
            for group, rows in sorted(parsed.items()):
                existing = (await session.execute(
                    select(EttRate).where(EttRate.group_code == group)
                )).scalars().all()
                if existing:
                    old = _stored(list(existing))
                else:
                    # Первая загрузка группы — сверка со снимком TWS (tnved_tree).
                    leaves = (await session.execute(
                        select(TnvedNode.code, TnvedNode.duty_rate).where(
                            TnvedNode.is_leaf.is_(True), TnvedNode.code.like(f"{group}%"),
                        )
                    )).all()
                    old = {code: ett.StoredRow(rate or "", (), None) for code, rate in leaves}
                if not rows or len(rows) < MIN_SHARE * len(old):
                    group_errors[group] = (f"разобрано {len(rows)} листьев при {len(old)} прежних — "
                                           "группа не применена")
                    continue
                file = page.groups[group]
                new = {code: ett.StoredRow(r.rate, r.notes, file.version) for code, r in rows.items()}
                old_all.update(old)
                new_all.update(new)
                await session.execute(delete(EttRate).where(EttRate.group_code == group))
                session.add_all(
                    EttRate(code=code, group_code=group, base_rate=r.rate, rate_raw=r.rate_raw,
                            notes=list(r.notes), unit=(r.unit or "")[:60] or None,
                            version=file.version, source_url=file.url)
                    for code, r in rows.items()
                )
                applied_groups[group] = file

            if new_notes is not None:
                # Примечания сменились — пересчитать и коды групп, которые не скачивались.
                untouched = (await session.execute(
                    select(EttRate).where(
                        func.cardinality(EttRate.notes) > 0,
                        EttRate.group_code.not_in(list(applied_groups) or [""]),
                    )
                )).scalars().all()
                stored = _stored(list(untouched))
                old_all.update(stored)
                new_all.update(stored)
                await session.execute(delete(EttNoteRow))
                session.add_all(
                    EttNoteRow(note_id=n.note_id, kind=n.kind,
                               periods=[ett.period_to_json(p) for p in n.periods],
                               text=n.text, version=page.notes.version)
                    for n in new_notes.values()
                )

            changes = ett.diff_editions(old_all, new_all, old_notes=old_notes,
                                        new_notes=notes_after, day=today)
            groups_meta = {**known, **{g: f.filename for g, f in applied_groups.items()}}
            versions = {g: (f.version.isoformat() if f.version else None) for g, f in page.groups.items()
                        if groups_meta.get(g) == f.filename}
            # Первая загрузка — базовая линия: весь перечень решений «новым» не считается.
            known_decisions = meta.get("decisions")
            new_decisions = ([d for d in page.decisions if d not in known_decisions]
                             if known_decisions else [])
            stats = {
                "groups_on_page": len(page.groups), "groups_downloaded": len(downloads),
                "groups_applied": sorted(applied_groups), "group_errors": group_errors,
                "notes_updated": new_notes is not None,
                "notes": len(notes_after),
                "rate_changes": sum(c.change == "rate" for c in changes),
                "added": sum(c.change == "added" for c in changes),
                "removed": sum(c.change == "removed" for c in changes),
                "new_decisions": new_decisions,
            }
            new_meta = {
                "groups": groups_meta,
                "versions": versions,
                "latest_version": max((v for v in versions.values() if v), default=None),
                "notes": page.notes.filename if new_notes is not None else meta.get("notes"),
                "notes_version": page.notes.version.isoformat() if page.notes.version else None,
                "decisions": list(page.decisions),
                "loaded_groups": len(groups_meta),
                "warning": (f"не применены группы: {', '.join(sorted(group_errors))}"
                            if group_errors else None),
            }
            run = await status.record_success(
                session, DATASET, trigger=trigger, started_at=started, stats=stats,
                changed=bool(applied_groups) or new_notes is not None, source_url=page_url,
                meta=new_meta,
            )
            for c in changes:
                session.add(RefChange(
                    dataset=DATASET, key=c.code, change=c.change, fields=["duty_rate"],
                    before={"rate": c.before} if c.before is not None else None,
                    after={"rate": c.after, **({"detail": c.detail} if c.detail else {})}
                    if c.after is not None else None,
                    effective_from=today, status="applied", run_id=run.id,
                ))
            if (alert := alerts.recovery_alert(state, was_alerted)) is not None:
                to_send.append(alert)
            if changes or group_errors or new_decisions:
                to_send.append(alerts.info_alert(DATASET, _summary(changes, group_errors, new_decisions)))
            result = "ok" if not group_errors else f"ok (не применены группы: {', '.join(sorted(group_errors))})"
    await alerts.send(to_send)
    return result


def _summary(changes: list[ett.EttChange], group_errors: dict[str, str], decisions: list[str]) -> str:
    parts: list[str] = []
    rate = [c for c in changes if c.change == "rate"]
    if rate:
        shown = "; ".join(f"{c.code}: {c.before} → {c.after}" for c in rate[:8])
        parts.append(f"ставки изменились у {len(rate)} кодов ({shown}{' …' if len(rate) > 8 else ''})")
    added = [c.code for c in changes if c.change == "added"]
    removed = [c.code for c in changes if c.change == "removed"]
    if added:
        parts.append(f"новые коды: {len(added)} ({', '.join(added[:8])}{' …' if len(added) > 8 else ''})")
    if removed:
        parts.append(f"исключены коды: {len(removed)} ({', '.join(removed[:8])}{' …' if len(removed) > 8 else ''})")
    if decisions:
        parts.append(f"новые решения в перечне ЕТТ: {', '.join(decisions[:6])}")
    if group_errors:
        parts.append(f"не применены группы: {', '.join(sorted(group_errors))}")
    return "сводная редакция ЕТТ — " + "; ".join(parts)


async def ett_snapshot(session: AsyncSession, codes: list[str]) -> tuple[
    dict[str, ett.StoredRow], dict[str, ett.EttNote], dict[str, dt.date | None],
]:
    """Строки ett_rates по кодам, примечания и редакции групп, загруженных из ЕТТ."""
    rows: dict[str, ett.StoredRow] = {}
    groups: dict[str, dt.date | None] = {}
    if codes:
        result = await session.execute(select(EttRate).where(EttRate.code.in_(codes)))
        rows = _stored(list(result.scalars().all()))
        wanted = sorted({c[:2] for c in codes})
        groups = dict((await session.execute(
            select(EttRate.group_code, func.max(EttRate.version))
            .where(EttRate.group_code.in_(wanted)).group_by(EttRate.group_code)
        )).all())
    notes = {
        n.note_id: ett.note_from_json(n.note_id, n.kind, n.periods, n.text)
        for n in (await session.execute(select(EttNoteRow))).scalars().all()
    } if rows else {}
    return rows, notes, groups
