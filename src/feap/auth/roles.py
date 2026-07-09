"""Роли в организации и их иерархия.

viewer    — читает досье/каталог/драфты, скачивает экспорт;
declarant — вся работа с ДТ: досье, классификация, драфты, каталог;
owner     — плюс управление организацией и участниками.
"""

from __future__ import annotations

ROLES = ("viewer", "declarant", "owner")
_RANK = {role: i for i, role in enumerate(ROLES)}

#: Роль dev-режима (запрос без токена в AUTH_MODE=optional) — полный доступ.
DEV_ROLE = "owner"


def is_valid_role(role: str) -> bool:
    return role in _RANK


def is_at_least(role: str, minimum: str) -> bool:
    return _RANK.get(role, -1) >= _RANK[minimum]
