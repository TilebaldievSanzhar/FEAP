"""Задания arq cron для справочников (регистрируются в feap.jobs.worker).

Расписание задаётся по Бишкеку (WorkerSettings.timezone). Каждое задание
берёт advisory lock по справочнику: два worker'а не сверяют одно и то же.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import delete, func, select

from feap.clock import bishkek_date, now_utc, today_bishkek
from feap.config import settings
from feap.db.models import RefChange, RefSyncRun
from feap.refsync import alerts, ett_sync, fx, remedies, status

logger = logging.getLogger(__name__)

#: Журнал запусков без изменений храним месяц, с изменениями — бессрочно.
NO_CHANGE_RETENTION_DAYS = 30


async def refsync_nbkr(ctx: dict) -> dict[str, str]:
    outcome = await fx.sync_feeds(ctx["db_session_factory"], trigger="cron")
    logger.info("refsync nbkr: %s", outcome)
    return outcome


async def refsync_nbkr_archive(ctx: dict) -> str:
    outcome = await fx.reconcile_archive(ctx["db_session_factory"], trigger="cron", days=10)
    logger.info("refsync nbkr archive: %s", outcome)
    return outcome


async def refsync_remedies(ctx: dict) -> str:
    outcome = await remedies.sync_remedies(ctx["db_session_factory"], trigger="cron")
    logger.info("refsync eaeu remedies: %s", outcome)
    return outcome


async def refsync_ett(ctx: dict) -> str:
    outcome = await ett_sync.sync_ett(ctx["db_session_factory"], trigger="cron")
    logger.info("refsync ETT: %s", outcome)
    return outcome


async def refsync_health(ctx: dict) -> int:
    """Гарантия ЕЭК истекает через ≤2 дня; изменения ждут подтверждения больше суток."""
    factory = ctx["db_session_factory"]
    to_send: list[alerts.Alert] = []
    today = today_bishkek()
    async with factory() as session, session.begin():
        for key, spec in status.DATASETS.items():
            if spec.guarantee != "eec":
                continue
            state = await status.get_dataset(session, key)
            meta = dict(state.meta or {})
            if meta.get("health_alerted_on") == today.isoformat():
                continue
            if state.last_success_at is None:
                text = "ещё ни разу не сверялся с источником — расчёты с этим справочником блокируются"
            else:
                until = bishkek_date(state.last_success_at) + dt.timedelta(
                    days=settings.REFDATA_EEC_GUARANTEE_DAYS)
                left = (until - today).days
                if left > 2:
                    continue
                text = (f"гарантия свежести до {until:%d.%m.%Y} (осталось {max(left, 0)} дн.) — "
                        "после этого расчёты с этим справочником блокируются")
            meta["health_alerted_on"] = today.isoformat()
            state.meta = meta
            to_send.append(alerts.info_alert(key, text))

        stale_pending = (await session.execute(
            select(func.count()).select_from(RefChange).where(
                RefChange.status == "pending",
                RefChange.detected_at < now_utc() - dt.timedelta(days=1),
            )
        )).scalar_one()
        reminder = await status.get_dataset(session, "eaeu_remedies")
        meta = dict(reminder.meta or {})
        if stale_pending and meta.get("pending_alerted_on") != today.isoformat():
            meta["pending_alerted_on"] = today.isoformat()
            reminder.meta = meta
            to_send.append(alerts.info_alert(
                "eaeu_remedies",
                f"{stale_pending} изменений справочников ждут подтверждения больше суток "
                "(python -m feap.refsync changes --pending)",
            ))

        await session.execute(delete(RefSyncRun).where(
            RefSyncRun.status.in_(("no_change", "skipped")),
            RefSyncRun.started_at < now_utc() - dt.timedelta(days=NO_CHANGE_RETENTION_DAYS),
        ))
    await alerts.send(to_send)
    return len(to_send)
