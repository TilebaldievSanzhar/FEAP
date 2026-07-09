"""JWT-токены доступа (HS256, PyJWT).

Токен несёт аккаунт и активную организацию: {sub, org, type, iat, exp}.
Роль в токен не кладём — членство перечитывается на каждом запросе, поэтому
смена роли/исключение из организации действуют мгновенно, без ожидания
истечения токена.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass

import jwt

from feap.config import settings

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
#: Предсказуемый секрет для локальной разработки (AUTH_MODE=optional без
#: AUTH_SECRET). В required-режиме пустой секрет — ошибка конфигурации.
#: ≥32 байт, чтобы не ловить InsecureKeyLengthWarning от PyJWT.
_DEV_SECRET = "feap-dev-secret-change-me-not-for-production-use"
_dev_secret_warned = False


class TokenError(ValueError):
    """Токен отсутствует, просрочен или не прошёл проверку."""


@dataclass(frozen=True)
class TokenClaims:
    account_id: uuid.UUID
    org_id: uuid.UUID


def _secret() -> str:
    secret = settings.AUTH_SECRET.strip()
    if secret:
        return secret
    if settings.AUTH_MODE == "required":
        raise RuntimeError(
            "AUTH_MODE=required требует непустой AUTH_SECRET (секрет подписи JWT)"
        )
    global _dev_secret_warned
    if not _dev_secret_warned:
        logger.warning(
            "AUTH_SECRET не задан — используется dev-секрет. "
            "Не годится ни для чего, кроме локальной разработки."
        )
        _dev_secret_warned = True
    return _DEV_SECRET


def create_access_token(
    account_id: uuid.UUID,
    org_id: uuid.UUID,
    *,
    ttl_minutes: int | None = None,
) -> tuple[str, int]:
    """Выпустить access-токен. Возвращает (token, expires_in_seconds)."""
    ttl = ttl_minutes if ttl_minutes is not None else settings.AUTH_TOKEN_TTL_MINUTES
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(account_id),
        "org": str(org_id),
        "type": "access",
        "iat": now,
        "exp": now + dt.timedelta(minutes=ttl),
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM), ttl * 60


def decode_access_token(token: str) -> TokenClaims:
    try:
        payload = jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Срок действия токена истёк") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Недействительный токен") from exc

    if payload.get("type") != "access":
        raise TokenError("Неверный тип токена")
    try:
        return TokenClaims(
            account_id=uuid.UUID(str(payload.get("sub"))),
            org_id=uuid.UUID(str(payload.get("org"))),
        )
    except (ValueError, TypeError) as exc:
        raise TokenError("Повреждённые данные токена") from exc
