"""AuthContext — единая точка «кто и в какой организации делает запрос».

Режимы (settings.AUTH_MODE):
- optional: Bearer-токен проверяется, если прислан (и тогда организация —
  из токена, членство перечитывается). Без токена — прежнее dev-поведение:
  X-Org-Id либо организация 'default', роль owner.
- required: без валидного токена — 401 (X-Org-Id игнорируется).

Роль всегда берётся из БД (не из токена): исключение из организации или
понижение роли действуют со следующего запроса.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from feap.api.deps import get_db
from feap.auth import service as auth_service
from feap.auth.roles import DEV_ROLE, is_at_least
from feap.auth.tokens import TokenError, decode_access_token
from feap.config import settings
from feap.db.models import Account, Organization
from feap.dt.errors import NotFoundError
from feap.orgs import resolve_org

_bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    org: Organization
    role: str
    account: Account | None = None  # None только в dev-режиме без токена

    @property
    def org_id(self) -> uuid.UUID:
        return self.org.id


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"},
    )


async def get_auth_context(
    session: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_org_id: str | None = Header(None, alias="X-Org-Id"),
) -> AuthContext:
    if credentials is not None:
        try:
            claims = decode_access_token(credentials.credentials)
        except TokenError as exc:
            raise _unauthorized(str(exc)) from exc

        account = await session.get(Account, claims.account_id)
        if account is None or not account.is_active:
            raise _unauthorized("Аккаунт не найден или деактивирован")

        membership = await auth_service.get_membership(
            session, account.id, claims.org_id
        )
        if membership is None:
            raise HTTPException(status_code=403, detail="Нет доступа к организации")
        org = await session.get(Organization, claims.org_id)
        if org is None:
            raise HTTPException(status_code=403, detail="Организация удалена")
        return AuthContext(org=org, role=membership.role, account=account)

    if settings.AUTH_MODE == "required":
        raise _unauthorized("Требуется авторизация (Bearer-токен)")

    try:
        org = await resolve_org(session, x_org_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AuthContext(org=org, role=DEV_ROLE, account=None)


def require_role(ctx: AuthContext, minimum: str) -> None:
    """403, если роль в текущей организации ниже требуемой."""
    if not is_at_least(ctx.role, minimum):
        raise HTTPException(
            status_code=403,
            detail=f"Недостаточно прав: требуется роль «{minimum}» или выше",
        )


def require_account(ctx: AuthContext) -> Account:
    """401, если запрос без аккаунта (dev-режим) обращается к операциям аккаунта."""
    if ctx.account is None:
        raise _unauthorized("Операция требует входа в аккаунт")
    return ctx.account
