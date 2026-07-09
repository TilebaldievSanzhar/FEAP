"""Пароли: argon2id roundtrip и устойчивость к мусорным хэшам."""

from __future__ import annotations

from feap.auth.passwords import hash_password, needs_rehash, verify_password


def test_roundtrip():
    h = hash_password("correct horse battery staple")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct horse battery staple")


def test_wrong_password_fails():
    h = hash_password("secret-123")
    assert not verify_password(h, "secret-124")
    assert not verify_password(h, "")


def test_salted_hashes_differ():
    assert hash_password("same") != hash_password("same")


def test_garbage_hash_fails_gracefully():
    assert not verify_password("not-a-hash", "whatever")
    assert needs_rehash("not-a-hash") is True


def test_fresh_hash_no_rehash():
    assert needs_rehash(hash_password("secret-123")) is False
