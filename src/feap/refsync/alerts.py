"""Оповещения о справочниках: лог всегда, Telegram — если настроен.

Решение «оповещать ли» принимается в транзакции записи состояния (там же
двигается alerted_at — троттлинг), отправка — после коммита: сеть не держит
транзакцию, а неудачная отправка теряет одно сообщение, но не данные.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import httpx

from feap.clock import BISHKEK_TZ
from feap.config import settings
from feap.db.models import RefDataset
from feap.refsync.status import DATASETS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Alert:
    dataset: str
    text: str


def _fmt(moment: dt.datetime | None) -> str:
    return moment.astimezone(BISHKEK_TZ).strftime("%d.%m.%Y %H:%M") if moment else "никогда"


def _title(key: str) -> str:
    spec = DATASETS.get(key)
    return spec.title if spec else key


def failure_alert(state: RefDataset, now: dt.datetime) -> Alert | None:
    """Сбой: после N неудач подряд, повтор не чаще REFDATA_ALERT_REPEAT_HOURS."""
    spec = DATASETS.get(state.dataset)
    threshold = spec.alert_after_failures if spec else 1
    if (state.consecutive_failures or 0) < threshold:
        return None
    repeat = dt.timedelta(hours=settings.REFDATA_ALERT_REPEAT_HOURS)
    if state.alerted_at and now - state.alerted_at < repeat:
        return None
    state.alerted_at = now
    return Alert(state.dataset, (
        f"Сбой обновления: {_title(state.dataset)} — {state.consecutive_failures} раз подряд. "
        f"Ошибка: {state.last_error or '—'}. Последняя успешная сверка: {_fmt(state.last_success_at)}."
    ))


def recovery_alert(state: RefDataset, was_alerted: bool) -> Alert | None:
    if not was_alerted:
        return None
    state.alerted_at = None
    return Alert(state.dataset, f"Восстановлено: {_title(state.dataset)} снова обновляется.")


def info_alert(dataset: str, text: str) -> Alert:
    return Alert(dataset, f"{_title(dataset)}: {text}")


async def send(alerts: list[Alert]) -> None:
    for alert in alerts:
        logger.warning("refsync alert [%s]: %s", alert.dataset, alert.text)
    token = settings.REFDATA_ALERT_TELEGRAM_BOT_TOKEN.strip()
    chat_id = settings.REFDATA_ALERT_TELEGRAM_CHAT_ID.strip()
    if not alerts or not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        for alert in alerts:
            try:
                resp = await client.post(url, json={"chat_id": chat_id, "text": f"FEAP · {alert.text}"})
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                # Без токена в логе: httpx кладёт URL (с токеном) в текст ошибки.
                logger.error("refsync alert to Telegram failed: %s", type(exc).__name__)
