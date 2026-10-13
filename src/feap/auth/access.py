"""Проверка принадлежности объекта организации запроса.

Правило единое для всех эндпоинтов: объект чужой организации неотличим от
несуществующего (404, а не 403 — не раскрываем факт существования UUID).
Объекты без владельца (org_id IS NULL, созданные до миграции 012) доступны
как раньше — они живут только до истечения TTL задач.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import HTTPException


def owner_matches(obj_org_id: uuid.UUID | None, org_id: uuid.UUID) -> bool:
    return obj_org_id is None or obj_org_id == org_id


def ensure_owner(obj_org_id: uuid.UUID | None, org_id: uuid.UUID, detail: str) -> None:
    if not owner_matches(obj_org_id, org_id):
        raise HTTPException(status_code=404, detail=detail)


def job_owner(job: Any) -> uuid.UUID | None:
    """Владелец задачи: колонка org_id, для старых задач — params['org_id']."""
    owner = getattr(job, "org_id", None)
    if owner is not None:
        return owner
    raw = (getattr(job, "params", None) or {}).get("org_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


_QUERY_TOKEN = re.compile(r"(access_token=)[^&\s\"]+")
_MASK = r"\1***"


class RedactQueryTokenFilter(logging.Filter):
    """Маскирует ?access_token= в access-логе.

    SSE /jobs/{id}/events получает JWT в query-строке (EventSource не умеет
    заголовки), а uvicorn пишет путь целиком — без фильтра токен оседает в логах.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _QUERY_TOKEN.sub(_MASK, a) if isinstance(a, str) else a
                for a in record.args
            )
        elif isinstance(record.msg, str):
            record.msg = _QUERY_TOKEN.sub(_MASK, record.msg)
        return True
