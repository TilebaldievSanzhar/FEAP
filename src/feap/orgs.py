"""Организации: dev-резолв без авторизации.

До B1 это была единственная точка входа (X-Org-Id | 'default'); теперь она
осталась fallback'ом для AUTH_MODE=optional без токена, чтобы локальная
разработка и существующие клиенты работали без изменений.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from feap.db.models import Organization
from feap.dt.errors import NotFoundError

DEFAULT_ORG_NAME = "default"


async def resolve_org(session: AsyncSession, x_org_id: str | None) -> Organization:
    if x_org_id:
        try:
            org_uuid = uuid.UUID(x_org_id)
        except ValueError:
            raise NotFoundError(f"Некорректный X-Org-Id: {x_org_id!r}") from None
        org = await session.get(Organization, org_uuid)
        if org is None:
            raise NotFoundError(f"Организация {x_org_id} не найдена")
        return org

    result = await session.execute(
        select(Organization).where(Organization.name == DEFAULT_ORG_NAME).limit(1)
    )
    org = result.scalar_one_or_none()
    if org is None:
        org = Organization(name=DEFAULT_ORG_NAME, kind="self")
        session.add(org)
        await session.flush()
    return org
