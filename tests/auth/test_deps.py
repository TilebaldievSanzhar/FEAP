"""get_auth_context: токен-путь, dev-fallback, required-режим (на фейках БД)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from feap.auth.deps import get_auth_context, require_account, require_role
from feap.auth.tokens import create_access_token
from feap.config import settings
from feap.db.models import Account, Organization


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """Ровно то, что трогает get_auth_context: get / execute / add / flush."""

    def __init__(self, *, get_map=None, execute_value=None):
        self._get_map = get_map or {}
        self._execute_value = execute_value
        self.added = []

    async def get(self, model, _pk):
        return self._get_map.get(model)

    async def execute(self, _stmt):
        return FakeResult(self._execute_value)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


ACCOUNT_ID = uuid.uuid4()
ORG_ID = uuid.uuid4()


def _bearer() -> HTTPAuthorizationCredentials:
    token, _ = create_access_token(ACCOUNT_ID, ORG_ID)
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _account(active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(id=ACCOUNT_ID, email="u@example.com",
                           name="U", is_active=active)


def _org() -> SimpleNamespace:
    return SimpleNamespace(id=ORG_ID, name="Org")


class TestTokenPath:
    async def test_valid_token_builds_context(self):
        session = FakeSession(
            get_map={Account: _account(), Organization: _org()},
            execute_value=SimpleNamespace(role="declarant"),  # членство
        )
        ctx = await get_auth_context(session, _bearer(), x_org_id=None)
        assert ctx.account.id == ACCOUNT_ID
        assert ctx.org_id == ORG_ID
        assert ctx.role == "declarant"

    async def test_token_wins_over_x_org_id(self):
        session = FakeSession(
            get_map={Account: _account(), Organization: _org()},
            execute_value=SimpleNamespace(role="viewer"),
        )
        ctx = await get_auth_context(session, _bearer(), x_org_id=str(uuid.uuid4()))
        assert ctx.org_id == ORG_ID  # X-Org-Id проигнорирован

    async def test_no_membership_is_403(self):
        session = FakeSession(
            get_map={Account: _account(), Organization: _org()},
            execute_value=None,
        )
        with pytest.raises(HTTPException) as exc:
            await get_auth_context(session, _bearer(), x_org_id=None)
        assert exc.value.status_code == 403

    async def test_inactive_account_is_401(self):
        session = FakeSession(get_map={Account: _account(active=False)})
        with pytest.raises(HTTPException) as exc:
            await get_auth_context(session, _bearer(), x_org_id=None)
        assert exc.value.status_code == 401

    async def test_bad_token_is_401(self):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="garbage")
        with pytest.raises(HTTPException) as exc:
            await get_auth_context(FakeSession(), creds, x_org_id=None)
        assert exc.value.status_code == 401


class TestNoTokenPath:
    async def test_required_mode_is_401(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_MODE", "required")
        with pytest.raises(HTTPException) as exc:
            await get_auth_context(FakeSession(), None, x_org_id=None)
        assert exc.value.status_code == 401

    async def test_optional_mode_falls_back_to_default_org(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_MODE", "optional")
        default_org = SimpleNamespace(id=uuid.uuid4(), name="default")
        session = FakeSession(execute_value=default_org)
        ctx = await get_auth_context(session, None, x_org_id=None)
        assert ctx.account is None
        assert ctx.role == "owner"
        assert ctx.org is default_org

    async def test_optional_mode_honours_x_org_id(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_MODE", "optional")
        org = _org()
        session = FakeSession(get_map={Organization: org})
        ctx = await get_auth_context(session, None, x_org_id=str(ORG_ID))
        assert ctx.org is org

    async def test_optional_mode_unknown_org_is_404(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_MODE", "optional")
        session = FakeSession(get_map={})
        with pytest.raises(HTTPException) as exc:
            await get_auth_context(session, None, x_org_id=str(uuid.uuid4()))
        assert exc.value.status_code == 404


class TestGuards:
    def _ctx(self, role: str, account=None):
        from feap.auth.deps import AuthContext

        return AuthContext(org=_org(), role=role, account=account)

    def test_require_role_allows_higher(self):
        require_role(self._ctx("owner"), "declarant")  # не бросает

    def test_require_role_blocks_lower(self):
        with pytest.raises(HTTPException) as exc:
            require_role(self._ctx("viewer"), "declarant")
        assert exc.value.status_code == 403

    def test_require_account_blocks_dev_mode(self):
        with pytest.raises(HTTPException) as exc:
            require_account(self._ctx("owner", account=None))
        assert exc.value.status_code == 401

    def test_require_account_returns_account(self):
        account = _account()
        assert require_account(self._ctx("owner", account=account)) is account
