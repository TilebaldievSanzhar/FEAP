"""API-схемы следа справочников: курс на день, проверки гарантий, статус."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, Field


class FxQuoteOut(BaseModel):
    """Курс, по которому посчитаны суммы, и откуда он взялся."""

    currency: str
    day: dt.date = Field(description="День, на который действует курс")
    rate: Decimal = Field(description="Сом за единицу валюты")
    source: str = Field(description="nbkr_daily | nbkr_weekly | nbkr_archive | manual | kgs")
    effective_from: dt.date | None = Field(None, description="Дата публикации НБКР")
    fetched_at: dt.datetime | None = Field(None, description="Когда курс получен от НБКР")
    published_at: dt.datetime | None = None


class DatasetCheckOut(BaseModel):
    """Гарантия справочника на дату расчёта."""

    dataset: str
    title: str
    guarantee: str = Field(description="eec — сверка с ЕЭК; attestation — подпись эксперта")
    checked_at: dt.datetime | None = None
    checked_by: str | None = None
    guaranteed_until: dt.date | None = None
    ok: bool
    message: str | None = None


class RefdataOut(BaseModel):
    """«Источник и дата сверки» для расчёта и проекта ДТ."""

    fx: list[FxQuoteOut] = Field(default_factory=list)
    datasets: list[DatasetCheckOut] = Field(default_factory=list)


class DataStatusEntry(BaseModel):
    dataset: str
    title: str
    source: str
    source_url: str | None = None
    cadence: str
    guarantee: str
    status: str = Field(description="ok | failing | unknown")
    last_attempt_at: dt.datetime | None = None
    last_success_at: dt.datetime | None = None
    last_change_at: dt.datetime | None = None
    last_error: str | None = None
    publication_date: dt.date | None = Field(None, description="Курсы: дата последней публикации НБКР")
    warning: str | None = None
    detail: str | None = Field(None, description="Что сейчас загружено (редакция ЕТТ и т.п.)")
    attested_at: dt.datetime | None = None
    attested_by: str | None = None
    attested_note: str | None = None
    guaranteed_until: dt.date | None = None
    ok: bool


class DataStatusOut(BaseModel):
    today: dt.date = Field(description="Сегодня по Бишкеку")
    datasets: list[DataStatusEntry]
    pending_changes: int = Field(0, description="Изменения справочников, ждущие подтверждения")
