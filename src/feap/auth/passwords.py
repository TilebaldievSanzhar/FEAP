"""Хэширование паролей: argon2id (argon2-cffi, параметры по умолчанию RFC 9106).

passlib не используется сознательно: он не поддерживается и ломается на
современных Python; argon2-cffi — канонический биндинг.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True, если хэш создан с устаревшими параметрами (обновить при логине)."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
