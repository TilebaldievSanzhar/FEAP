"""API авторизации и управления участниками организации.

POST   /api/v1/auth/register             — аккаунт + организация + токен
POST   /api/v1/auth/login                — вход (email+пароль[, org_id])
GET    /api/v1/auth/me                   — аккаунт, активная организация, все организации
POST   /api/v1/auth/switch-org           — токен для другой своей организации
GET    /api/v1/orgs/current/members      — участники (owner)
POST   /api/v1/orgs/current/members      — добавить по email (owner)
PATCH  /api/v1/orgs/current/members/{id} — сменить роль (owner)
DELETE /api/v1/orgs/current/members/{id} — удалить (owner)

register/login публичны в обоих режимах AUTH_MODE; остальное требует токен
(или dev-режим для чтения ctx, но операции с участниками — только с аккаунтом).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from feap.api.deps import get_db
from feap.auth import service as auth
from feap.auth.deps import AuthContext, get_auth_context, require_account, require_role
from feap.auth.schemas import (
    AccountOut,
    LoginIn,
    MeOut,
    MemberAddIn,
    MemberOut,
    MemberRoleIn,
    MembersOut,
    OrgBrief,
    RegisterIn,
    SwitchOrgIn,
    TokenOut,
)
from feap.auth.tokens import create_access_token
from feap.db.models import Account, Membership, Organization

router = APIRouter(prefix="/api/v1", tags=["auth"])


def _token_out(account: Account, org: Organization, membership: Membership) -> TokenOut:
    token, expires_in = create_access_token(account.id, org.id)
    return TokenOut(
        access_token=token,
        expires_in=expires_in,
        account=AccountOut(id=account.id, email=account.email, name=account.name),
        org=OrgBrief(id=org.id, name=org.name, role=membership.role),
    )


@router.post("/auth/register", response_model=TokenOut, status_code=201)
async def register(
    payload: RegisterIn,
    session: AsyncSession = Depends(get_db),
) -> TokenOut:
    try:
        account, org, membership = await auth.register(
            session,
            email=payload.email,
            password=payload.password,
            name=payload.name,
            org_name=payload.org_name,
        )
    except auth.EmailTaken as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return _token_out(account, org, membership)


@router.post("/auth/login", response_model=TokenOut)
async def login(
    payload: LoginIn,
    session: AsyncSession = Depends(get_db),
) -> TokenOut:
    try:
        account = await auth.authenticate(
            session, email=payload.email, password=payload.password
        )
        membership = await auth.membership_for_login(session, account, payload.org_id)
    except auth.AuthFailed as exc:
        raise HTTPException(
            status_code=401, detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except auth.MembershipNotFound as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    org = await session.get(Organization, membership.org_id)
    assert org is not None
    await session.commit()  # rehash пароля / автосозданная личная организация
    return _token_out(account, org, membership)


@router.get("/auth/me", response_model=MeOut)
async def me(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db),
) -> MeOut:
    account = require_account(ctx)
    orgs = await auth.list_account_orgs(session, account.id)
    return MeOut(
        account=AccountOut(id=account.id, email=account.email, name=account.name),
        org=OrgBrief(id=ctx.org.id, name=ctx.org.name, role=ctx.role),
        orgs=[OrgBrief(id=o.id, name=o.name, role=m.role) for m, o in orgs],
    )


@router.post("/auth/switch-org", response_model=TokenOut)
async def switch_org(
    payload: SwitchOrgIn,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db),
) -> TokenOut:
    account = require_account(ctx)
    membership = await auth.get_membership(session, account.id, payload.org_id)
    if membership is None:
        raise HTTPException(status_code=403, detail="Нет доступа к организации")
    org = await session.get(Organization, payload.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    return _token_out(account, org, membership)


# ── Участники текущей организации ────────────────────────────────────────────

def _member_out(membership: Membership, account: Account) -> MemberOut:
    return MemberOut(
        account_id=account.id,
        email=account.email,
        name=account.name,
        role=membership.role,
        created_at=membership.created_at,
    )


@router.get("/orgs/current/members", response_model=MembersOut)
async def list_members(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db),
) -> MembersOut:
    require_account(ctx)
    require_role(ctx, "owner")
    rows = await auth.list_members(session, ctx.org_id)
    return MembersOut(items=[_member_out(m, a) for m, a in rows])


@router.post("/orgs/current/members", response_model=MemberOut, status_code=201)
async def add_member(
    payload: MemberAddIn,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db),
) -> MemberOut:
    require_account(ctx)
    require_role(ctx, "owner")
    try:
        membership, account = await auth.add_member(
            session, ctx.org_id, email=payload.email, role=payload.role
        )
    except auth.AccountNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except auth.MembershipExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except auth.InvalidRole as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return _member_out(membership, account)


@router.patch("/orgs/current/members/{account_id}", response_model=MemberOut)
async def change_member_role(
    account_id: uuid.UUID,
    payload: MemberRoleIn,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db),
) -> MemberOut:
    require_account(ctx)
    require_role(ctx, "owner")
    try:
        membership = await auth.change_member_role(
            session, ctx.org_id, account_id, role=payload.role
        )
    except auth.MembershipNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except auth.LastOwnerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except auth.InvalidRole as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    account = await session.get(Account, account_id)
    assert account is not None
    await session.commit()
    return _member_out(membership, account)


@router.delete("/orgs/current/members/{account_id}", status_code=204)
async def remove_member(
    account_id: uuid.UUID,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db),
) -> Response:
    require_account(ctx)
    require_role(ctx, "owner")
    try:
        await auth.remove_member(session, ctx.org_id, account_id)
    except auth.MembershipNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except auth.LastOwnerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return Response(status_code=204)
