"""Pydantic-схемы auth API."""

from __future__ import annotations

import datetime as dt
import re
import uuid

from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _EmailMixin(BaseModel):
    email: str = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Некорректный email")
        return v


class RegisterIn(_EmailMixin):
    password: str = Field(min_length=8, max_length=256)
    name: str | None = Field(None, max_length=200)
    org_name: str | None = Field(None, max_length=200)


class LoginIn(_EmailMixin):
    password: str = Field(min_length=1, max_length=256)
    org_id: uuid.UUID | None = Field(
        None, description="Организация для входа; по умолчанию — первая"
    )


class SwitchOrgIn(BaseModel):
    org_id: uuid.UUID


class AccountOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None


class OrgBrief(BaseModel):
    id: uuid.UUID
    name: str
    role: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Секунды до истечения")
    account: AccountOut
    org: OrgBrief


class MeOut(BaseModel):
    account: AccountOut
    org: OrgBrief = Field(description="Активная организация (из токена)")
    orgs: list[OrgBrief]


class MemberAddIn(_EmailMixin):
    role: str = Field("declarant", pattern=r"^(viewer|declarant|owner)$")


class MemberRoleIn(BaseModel):
    role: str = Field(pattern=r"^(viewer|declarant|owner)$")


class MemberOut(BaseModel):
    account_id: uuid.UUID
    email: str
    name: str | None
    role: str
    created_at: dt.datetime


class MembersOut(BaseModel):
    items: list[MemberOut]
