"""CLI справочников: python -m feap.refsync <команда>.

  status                          состояние всех справочников
  sync nbkr|archive|remedies|ett  сверить сейчас (archive: --days N; ett: --force — все группы)
  backfill-nbkr --days N          курсы 5 ежедневных валют из архива НБКР за N дней
  changes [--pending] [--limit N] изменения справочников
  approve ID --by ИМЯ [--note …]  подтвердить изменение (ставки сверены с решением)
  reject ID --by ИМЯ [--note …]   отклонить (ставки разобраны неверно → ручной ввод)
  attest DATASET --by ИМЯ [--at ГГГГ-ММ-ДД] [--note …]
                                  эксперт подтверждает актуальность ручного справочника

В Docker: docker compose exec app python -m feap.refsync status
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys

from sqlalchemy import select

from feap.clock import BISHKEK_TZ, now_utc, today_bishkek
from feap.db.engine import async_session, engine
from feap.db.models import RefChange
from feap.refsync import ett_sync, fx, remedies, status


def _fmt(moment: dt.datetime | None) -> str:
    return moment.astimezone(BISHKEK_TZ).strftime("%d.%m.%Y %H:%M") if moment else "—"


async def cmd_status() -> None:
    async with async_session() as session:
        states = await status.load_datasets(session)
        pending = (await session.execute(
            select(RefChange).where(RefChange.status == "pending")
        )).scalars().all()
    print(f"Сегодня по Бишкеку: {today_bishkek():%d.%m.%Y}\n")
    for key, spec in status.DATASETS.items():
        st = states.get(key)
        line = f"{key:<17} {(st.status if st else 'unknown'):<8} {spec.title}"
        print(line)
        if st is None:
            print("                  ещё не сверялся")
            continue
        if spec.guarantee == "attestation":
            print(f"                  подтверждено: {_fmt(st.attested_at)}"
                  + (f" ({st.attested_by})" if st.attested_by else ""))
        else:
            print(f"                  успех: {_fmt(st.last_success_at)} · попытка: "
                  f"{_fmt(st.last_attempt_at)} · изменение: {_fmt(st.last_change_at)}")
            meta = st.meta or {}
            if meta.get("publication_date"):
                print(f"                  публикация НБКР: {meta['publication_date']}")
            if meta.get("warning"):
                print(f"                  предупреждение: {meta['warning']}")
        if st.last_error and st.status == "failing":
            print(f"                  ошибка ({st.consecutive_failures} подряд): {st.last_error}")
    print(f"\nИзменений ждут подтверждения: {len(pending)}")


async def cmd_sync(target: str, days: int, force: bool = False) -> None:
    if target == "ett":
        print(await ett_sync.sync_ett(async_session, trigger="manual", force=force))
    elif target == "nbkr":
        print(await fx.sync_feeds(async_session, trigger="manual"))
    elif target == "archive":
        print(await fx.reconcile_archive(async_session, trigger="manual", days=days))
    elif target == "remedies":
        print(await remedies.sync_remedies(async_session, trigger="manual"))


async def cmd_backfill(days: int) -> None:
    today = today_bishkek()
    start = today - dt.timedelta(days=days)
    # Архив отдаёт диапазон целиком; режем на годовые куски, чтобы страницы были небольшими.
    chunk_start = start
    while chunk_start <= today:
        chunk_end = min(chunk_start + dt.timedelta(days=365), today + dt.timedelta(days=1))
        print(chunk_start, chunk_end, await fx.reconcile_archive(
            async_session, trigger="manual", start=chunk_start, end=chunk_end,
        ))
        chunk_start = chunk_end + dt.timedelta(days=1)


async def cmd_changes(pending: bool, limit: int) -> None:
    async with async_session() as session:
        query = select(RefChange).order_by(RefChange.id.desc()).limit(limit)
        if pending:
            query = query.where(RefChange.status == "pending")
        rows = (await session.execute(query)).scalars().all()
    if not rows:
        print("Нет изменений")
    for c in rows:
        print(f"#{c.id} {c.dataset} {c.key} {c.change} [{c.status}] {_fmt(c.detected_at)}"
              + (f" поля: {', '.join(c.fields)}" if c.fields else ""))
        if c.before:
            print("   было:  " + json.dumps(c.before, ensure_ascii=False)[:600])
        if c.after:
            after = {k: v for k, v in c.after.items() if k != "rate_text"}
            print("   стало: " + json.dumps(after, ensure_ascii=False)[:600])
            if c.after.get("rate_text"):
                print("   текст формы меры:\n      " + c.after["rate_text"].replace("\n", "\n      ")[:1500])
        if c.decided_by:
            print(f"   решение: {c.decided_by} {_fmt(c.decided_at)} {c.note or ''}")


async def cmd_decide(change_id: int, approve: bool, by: str, note: str | None) -> None:
    async with async_session() as session, session.begin():
        change = await session.get(RefChange, change_id)
        if change is None:
            sys.exit(f"Изменение #{change_id} не найдено")
        if change.dataset != remedies.DATASET:
            sys.exit(f"Подтверждение для {change.dataset} пока не поддерживается")
        try:
            print(await remedies.decide(session, change, approve=approve, by=by, note=note))
        except ValueError as exc:
            sys.exit(f"Не применено: {exc}")


async def cmd_attest(dataset: str, by: str, at: dt.date | None, note: str | None) -> None:
    spec = status.DATASETS.get(dataset)
    if spec is None or spec.guarantee != "attestation":
        manual = [k for k, s in status.DATASETS.items() if s.guarantee == "attestation"]
        sys.exit(f"Подпись эксперта — только для ручных справочников: {', '.join(manual)}")
    moment = (dt.datetime.combine(at, dt.time(12, 0), tzinfo=BISHKEK_TZ) if at else now_utc())
    async with async_session() as session, session.begin():
        state = await status.get_dataset(session, dataset)
        state.attested_at = moment
        state.attested_by = by
        state.attested_note = note
        state.status = "ok"
    print(f"{spec.title}: подтверждено {_fmt(moment)} ({by})")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m feap.refsync", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_sync = sub.add_parser("sync")
    p_sync.add_argument("target", choices=["nbkr", "archive", "remedies", "ett"])
    p_sync.add_argument("--days", type=int, default=10)
    p_sync.add_argument("--force", action="store_true")
    p_back = sub.add_parser("backfill-nbkr")
    p_back.add_argument("--days", type=int, required=True)
    p_ch = sub.add_parser("changes")
    p_ch.add_argument("--pending", action="store_true")
    p_ch.add_argument("--limit", type=int, default=30)
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("id", type=int)
        p.add_argument("--by", required=True)
        p.add_argument("--note")
    p_at = sub.add_parser("attest")
    p_at.add_argument("dataset")
    p_at.add_argument("--by", required=True)
    p_at.add_argument("--at", type=dt.date.fromisoformat)
    p_at.add_argument("--note")
    args = parser.parse_args()

    async def run() -> None:
        try:
            if args.cmd == "status":
                await cmd_status()
            elif args.cmd == "sync":
                await cmd_sync(args.target, args.days, args.force)
            elif args.cmd == "backfill-nbkr":
                await cmd_backfill(args.days)
            elif args.cmd == "changes":
                await cmd_changes(args.pending, args.limit)
            elif args.cmd in ("approve", "reject"):
                await cmd_decide(args.id, args.cmd == "approve", args.by, args.note)
            elif args.cmd == "attest":
                await cmd_attest(args.dataset, args.by, args.at, args.note)
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
