"""Метеринг: единая точка записи usage_events.

Биллинг (Track B2) будет читать агрегаты по org_id/kind — поэтому события
пишутся из всех платных путей (классификация, OCR, драфты ДТ, экспорт),
даже пока авторизации нет (org_id допускает NULL).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from feap.db.models import UsageEvent


def record_usage(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | None,
    kind: str,
    quantity: Decimal | int = 1,
    ref_type: str | None = None,
    ref_id: str | None = None,
    meta: dict | None = None,
) -> None:
    session.add(UsageEvent(
        org_id=org_id, kind=kind, quantity=Decimal(quantity),
        ref_type=ref_type, ref_id=ref_id, meta=meta or {},
    ))
