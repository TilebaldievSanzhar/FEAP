"""Курсы НБКР вручную — та же сверка, что делает worker каждые 15 минут.

Запуск: `python scripts/fetch/nbkr_rates.py` (или `python -m feap.refsync sync nbkr`).
Пишет курс на каждый календарный день из daily.xml/weekly.xml (refsync.nbkr):
курсы со старым источником 'NBKR' расчёт больше не читает (миграция 014).
Сверка с архивом и дозаполнение дней: `python -m feap.refsync sync archive --days 30`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from feap.db.engine import async_session, engine  # noqa: E402
from feap.refsync.fx import sync_feeds  # noqa: E402


async def _main() -> int:
    try:
        outcome = await sync_feeds(async_session, trigger="manual")
    finally:
        await engine.dispose()
    print(outcome)
    return 0 if all(v == "ok" or v == "skipped" for v in outcome.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
