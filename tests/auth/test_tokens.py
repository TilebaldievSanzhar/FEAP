"""JWT: roundtrip, истечение, подделка, тип токена."""

from __future__ import annotations

import uuid

import jwt as pyjwt
import pytest

from feap.auth import tokens
from feap.auth.tokens import TokenError, create_access_token, decode_access_token

ACCOUNT = uuid.uuid4()
ORG = uuid.uuid4()


def test_roundtrip():
    token, expires_in = create_access_token(ACCOUNT, ORG)
    assert expires_in > 0
    claims = decode_access_token(token)
    assert claims.account_id == ACCOUNT
    assert claims.org_id == ORG


def test_expired_token_rejected():
    token, _ = create_access_token(ACCOUNT, ORG, ttl_minutes=-1)
    with pytest.raises(TokenError, match="истёк"):
        decode_access_token(token)


def test_tampered_token_rejected():
    token, _ = create_access_token(ACCOUNT, ORG)
    header, payload, sig = token.split(".")
    with pytest.raises(TokenError):
        decode_access_token(f"{header}.{payload}x.{sig}")


def test_wrong_signature_rejected():
    forged = pyjwt.encode(
        {"sub": str(ACCOUNT), "org": str(ORG), "type": "access"},
        "another-secret-that-is-long-enough-32b!", algorithm="HS256",
    )
    with pytest.raises(TokenError):
        decode_access_token(forged)


def test_wrong_type_rejected():
    token = pyjwt.encode(
        {"sub": str(ACCOUNT), "org": str(ORG), "type": "refresh"},
        tokens._secret(), algorithm="HS256",
    )
    with pytest.raises(TokenError, match="тип"):
        decode_access_token(token)


def test_corrupt_claims_rejected():
    token = pyjwt.encode(
        {"sub": "not-a-uuid", "org": str(ORG), "type": "access"},
        tokens._secret(), algorithm="HS256",
    )
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_garbage_rejected():
    with pytest.raises(TokenError):
        decode_access_token("garbage.token.here")
