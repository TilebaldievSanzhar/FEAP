"""Auth-сервис: регистрация, вход, выбор организации, участники.

Правила:
- email нормализуется в нижний регистр; уникальность — по lower(email);
- ошибки входа неразличимы снаружи (один и тот же AuthFailed для
  «нет такого email» и «неверный пароль»);
- у аккаунта всегда есть хотя бы одна организация: регистрация создаёт её,
  а вход при нуле членств создаёт личную заново;
- организацию нельзя оставить без owner'а (guard на смене роли/удалении).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from feap.auth.passwords import hash_password, needs_rehash, verify_password
from feap.auth.roles import is_valid_role
from feap.db.models import Account, Membership, Organization


class AuthError(Exception):
    """База для доменных ошибок auth."""


class EmailTaken(AuthError):
    pass


class AuthFailed(AuthError):
    """Неверный email или пароль (намеренно неразличимо)."""


class MembershipNotFound(AuthError):
    pass


class MembershipExists(AuthError):
    pass


class AccountNotFound(AuthError):
    pass


class LastOwnerError(AuthError):
    """Операция оставила бы организацию без owner'а."""


class InvalidRole(AuthError):
    pass


# ── Чистые функции ───────────────────────────────────────────────────────────

def normalize_email(email: str) -> str:
    return email.strip().lower()


def would_lose_last_owner(
    current_role: str, new_role: str | None, owners_count: int
) -> bool:
    """True, если смена роли (или удаление, new_role=None) убирает последнего owner'а."""
    if current_role != "owner":
        return False
    if new_role == "owner":
        return False
    return owners_count <= 1


# ── Аккаунты ─────────────────────────────────────────────────────────────────

_dummy_hash: str | None = None


def _timing_equalizer_hash() -> str:
    """Ленивый dummy-хэш: выравнивает время ответа для несуществующих email."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("timing-equalizer")
    return _dummy_hash


async def get_account_by_email(session: AsyncSession, email: str) -> Account | None:
    normalized = normalize_email(email)
    result = await session.execute(
        select(Account).where(func.lower(Account.email) == normalized).limit(1)
    )
    return result.scalar_one_or_none()


async def register(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    name: str | None = None,
    org_name: str | None = None,
) -> tuple[Account, Organization, Membership]:
    """Создать аккаунт + организацию + членство owner. Без commit — на вызывающем."""
    normalized = normalize_email(email)
    if await get_account_by_email(session, normalized) is not None:
        raise EmailTaken(f"Email {normalized} уже зарегистрирован")

    account = Account(
        email=normalized,
        password_hash=hash_password(password),
        name=(name or "").strip() or None,
    )
    session.add(account)

    org = Organization(
        name=(org_name or "").strip() or f"Организация {account.name or normalized}",
        kind="self",
    )
    session.add(org)
    await session.flush()

    membership = Membership(account_id=account.id, org_id=org.id, role="owner")
    session.add(membership)
    await session.flush()
    return account, org, membership


async def authenticate(session: AsyncSession, *, email: str, password: str) -> Account:
    account = await get_account_by_email(session, email)
    if account is None or not account.is_active:
        # Проверяем dummy-хэш, чтобы время ответа не выдавало,
        # существует ли email.
        verify_password(_timing_equalizer_hash(), password)
        raise AuthFailed("Неверный email или пароль")
    if not verify_password(account.password_hash, password):
        raise AuthFailed("Неверный email или пароль")
    if needs_rehash(account.password_hash):
        account.password_hash = hash_password(password)
        await session.flush()
    return account


# ── Членства / организации ───────────────────────────────────────────────────

async def get_membership(
    session: AsyncSession, account_id: uuid.UUID, org_id: uuid.UUID
) -> Membership | None:
    result = await session.execute(
        select(Membership).where(
            Membership.account_id == account_id,
            Membership.org_id == org_id,
        ).limit(1)
    )
    return result.scalar_one_or_none()


async def list_account_orgs(
    session: AsyncSession, account_id: uuid.UUID
) -> list[tuple[Membership, Organization]]:
    result = await session.execute(
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.org_id)
        .where(Membership.account_id == account_id)
        .order_by(Membership.created_at)
    )
    return [(m, o) for m, o in result.all()]


async def membership_for_login(
    session: AsyncSession, account: Account, org_id: uuid.UUID | None
) -> Membership:
    """Членство для входа: указанная организация или первая по времени.

    Аккаунт без единого членства получает новую личную организацию —
    инвариант «всегда есть организация» держится и после исключения отовсюду.
    """
    if org_id is not None:
        membership = await get_membership(session, account.id, org_id)
        if membership is None:
            raise MembershipNotFound(f"Нет доступа к организации {org_id}")
        return membership

    orgs = await list_account_orgs(session, account.id)
    if orgs:
        return orgs[0][0]

    org = Organization(name=f"Организация {account.name or account.email}", kind="self")
    session.add(org)
    await session.flush()
    membership = Membership(account_id=account.id, org_id=org.id, role="owner")
    session.add(membership)
    await session.flush()
    return membership


# ── Участники организации (owner-only на уровне роутера) ────────────────────

async def list_members(
    session: AsyncSession, org_id: uuid.UUID
) -> list[tuple[Membership, Account]]:
    result = await session.execute(
        select(Membership, Account)
        .join(Account, Account.id == Membership.account_id)
        .where(Membership.org_id == org_id)
        .order_by(Membership.created_at)
    )
    return [(m, a) for m, a in result.all()]


async def _count_owners(session: AsyncSession, org_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count()).select_from(Membership).where(
            Membership.org_id == org_id, Membership.role == "owner",
        )
    )
    return result.scalar_one()


async def add_member(
    session: AsyncSession, org_id: uuid.UUID, *, email: str, role: str
) -> tuple[Membership, Account]:
    if not is_valid_role(role):
        raise InvalidRole(f"Недопустимая роль: {role!r}")
    account = await get_account_by_email(session, email)
    if account is None:
        raise AccountNotFound(
            f"Аккаунт {normalize_email(email)} не найден — участник должен "
            "сначала зарегистрироваться"
        )
    if await get_membership(session, account.id, org_id) is not None:
        raise MembershipExists(f"{account.email} уже состоит в организации")
    membership = Membership(account_id=account.id, org_id=org_id, role=role)
    session.add(membership)
    await session.flush()
    return membership, account


async def change_member_role(
    session: AsyncSession, org_id: uuid.UUID, account_id: uuid.UUID, *, role: str
) -> Membership:
    if not is_valid_role(role):
        raise InvalidRole(f"Недопустимая роль: {role!r}")
    membership = await get_membership(session, account_id, org_id)
    if membership is None:
        raise MembershipNotFound(f"Участник {account_id} не найден в организации")
    owners = await _count_owners(session, org_id)
    if would_lose_last_owner(membership.role, role, owners):
        raise LastOwnerError("Нельзя снять роль у последнего owner'а организации")
    membership.role = role
    await session.flush()
    return membership


async def remove_member(
    session: AsyncSession, org_id: uuid.UUID, account_id: uuid.UUID
) -> None:
    membership = await get_membership(session, account_id, org_id)
    if membership is None:
        raise MembershipNotFound(f"Участник {account_id} не найден в организации")
    owners = await _count_owners(session, org_id)
    if would_lose_last_owner(membership.role, None, owners):
        raise LastOwnerError("Нельзя удалить последнего owner'а организации")
    await session.delete(membership)
    await session.flush()
