"""Справочники как объекты: реестр, состояние (ref_datasets), журнал запусков.

Реестр DATASETS — в коде (что за справочник, откуда, как гарантируется);
состояние — в БД (когда сверялись и чем закончилось). Запись журнала и
состояния — в той же транзакции, что и сами данные.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from feap.clock import now_utc
from feap.db.models import RefDataset, RefSyncRun

#: Как обеспечивается гарантия «на дату нет неизвестного нам значения»:
#: fx — курс на каждый день (refsync.fx); eec — сверка с ЕЭК не старше N дней
#: (решения вступают в силу ≥10 дней после опубликования); attestation —
#: подпись эксперта не старше N дней (акты КР, снимки без ленты изменений).
Guarantee = Literal["fx", "eec", "attestation", "none"]


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    title: str
    source: str
    source_url: str | None
    cadence: str
    guarantee: Guarantee
    #: Сколько неудач подряд терпим до оповещения (опрос раз в 15 мин → 3).
    alert_after_failures: int = 1
    #: Участвует в расчёте платежей (проверяется guard'ом).
    money: bool = True
    #: eec: настройка с горизонтом гарантии в днях (по умолчанию REFDATA_EEC_GUARANTEE_DAYS).
    horizon_setting: str | None = None


DATASETS: dict[str, DatasetSpec] = {spec.key: spec for spec in (
    DatasetSpec(
        "nbkr_daily", "Курсы НБКР (USD, EUR, CNY, KZT, RUB)",
        "Национальный банк КР, ежедневные официальные курсы", "https://www.nbkr.kg/XML/daily.xml",
        "каждые 15 минут", "fx", alert_after_failures=3,
    ),
    DatasetSpec(
        "nbkr_weekly", "Курсы НБКР (остальные валюты)",
        "Национальный банк КР, еженедельные официальные курсы", "https://www.nbkr.kg/XML/weekly.xml",
        "каждые 15 минут", "fx", alert_after_failures=3,
    ),
    DatasetSpec(
        "nbkr_archive", "Сверка курсов с архивом НБКР",
        "Национальный банк КР, архив официальных курсов",
        "https://www.nbkr.kg/index1.jsp?item=1562&lang=RUS",
        "ежедневно в 07:10", "none", money=False,
    ),
    DatasetSpec(
        "eaeu_remedies", "Меры защиты рынка ЕАЭС",
        "Реестр мер ЕЭК (антидемпинговые, специальные, компенсационные)",
        "https://remedies.eaeunion.org/dimd/ru/security/common",
        "ежедневно в 06:40", "eec",
    ),
    DatasetSpec(
        "ett_rates", "Ставки ввозных пошлин ЕТТ ЕАЭС",
        "Сводная редакция ЕТТ ЕАЭС на сайте ЕЭК: PDF групп и примечания к ЕТТ",
        "https://eec.eaeunion.org/comission/department/catr/ett/",
        "ежедневно в 06:50", "eec", horizon_setting="REFDATA_ETT_GUARANTEE_DAYS",
    ),
    DatasetSpec(
        "vat_benefits", "Льготы по НДС на импорт",
        "НК КР, постановления Кабмина КР № 816, 249, 264", "https://cbd.minjust.gov.kg/",
        "подтверждение экспертом", "attestation",
    ),
    DatasetSpec(
        "excise_rates", "Ставки акциза",
        "Налоговый кодекс КР", "https://cbd.minjust.gov.kg/",
        "подтверждение экспертом", "attestation",
    ),
    DatasetSpec(
        "customs_fee", "Сбор за таможенные операции",
        "ПП КР № 79, п. 38 (0,4%, от 5 до 2500 РП)", "https://cbd.minjust.gov.kg/",
        "подтверждение экспертом", "attestation",
    ),
    DatasetSpec(
        "trade_agreements", "Режимы ЗСТ ЕАЭС",
        "Соглашения ЕАЭС о свободной торговле", "https://eec.eaeunion.org/",
        "подтверждение экспертом", "attestation",
    ),
)}

#: Справочники, без которых не считаются деньги (порядок — для интерфейса).
MONEY_DATASETS: tuple[str, ...] = tuple(k for k, s in DATASETS.items() if s.money)


async def load_datasets(session: AsyncSession) -> dict[str, RefDataset]:
    rows = (await session.execute(select(RefDataset))).scalars().all()
    return {r.dataset: r for r in rows}


async def get_dataset(session: AsyncSession, key: str) -> RefDataset:
    """Строка состояния; создаётся при первом обращении (без коммита)."""
    row = await session.get(RefDataset, key)
    if row is None:
        row = RefDataset(dataset=key, status="unknown", consecutive_failures=0, meta={})
        session.add(row)
        await session.flush()
    return row


async def try_lock(session: AsyncSession, key: str) -> bool:
    """Транзакционный advisory lock: два worker'а не сверяют один справочник разом."""
    got = await session.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"), {"k": f"refsync:{key}"},
    )
    return bool(got.scalar())


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


async def last_raw_sha(session: AsyncSession, key: str) -> str | None:
    result = await session.execute(
        select(RefSyncRun.raw_sha256)
        .where(RefSyncRun.dataset == key, RefSyncRun.raw_sha256.is_not(None))
        .order_by(RefSyncRun.started_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def record_success(
    session: AsyncSession,
    key: str,
    *,
    trigger: str,
    started_at: dt.datetime,
    stats: dict,
    changed: bool,
    source_url: str | None = None,
    raw: bytes | None = None,
    meta: dict | None = None,
) -> RefSyncRun:
    """Успешный запуск: журнал + состояние. Сырой ответ храним, только если он новый."""
    now = now_utc()
    digest = sha256(raw) if raw is not None else None
    keep_raw = digest is not None and digest != await last_raw_sha(session, key)
    run = RefSyncRun(
        dataset=key, trigger=trigger, status="ok" if changed else "no_change",
        started_at=started_at, finished_at=now, stats=stats, source_url=source_url,
        raw_sha256=digest, raw_gzip=gzip.compress(raw) if keep_raw and raw is not None else None,
    )
    session.add(run)
    state = await get_dataset(session, key)
    state.status = "ok"
    state.last_attempt_at = now
    state.last_success_at = now
    state.consecutive_failures = 0
    state.last_error = None
    if changed:
        state.last_change_at = now
    if meta is not None:
        state.meta = {**(state.meta or {}), **meta}
    await session.flush()
    return run


async def record_failure(
    session: AsyncSession,
    key: str,
    *,
    trigger: str,
    started_at: dt.datetime,
    error: str,
    source_url: str | None = None,
    stats: dict | None = None,
) -> RefDataset:
    now = now_utc()
    session.add(RefSyncRun(
        dataset=key, trigger=trigger, status="failed", started_at=started_at,
        finished_at=now, stats=stats or {}, error=error[:2000], source_url=source_url,
    ))
    state = await get_dataset(session, key)
    state.status = "failing"
    state.last_attempt_at = now
    state.consecutive_failures = (state.consecutive_failures or 0) + 1
    state.last_error = error[:2000]
    await session.flush()
    return state
