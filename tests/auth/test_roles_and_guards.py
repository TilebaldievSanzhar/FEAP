"""Роли: иерархия; guard последнего owner'а; нормализация email."""

from __future__ import annotations

import pytest

from feap.auth.roles import DEV_ROLE, is_at_least, is_valid_role
from feap.auth.service import normalize_email, would_lose_last_owner


class TestRoles:
    @pytest.mark.parametrize("role,minimum,expected", [
        ("viewer", "viewer", True),
        ("viewer", "declarant", False),
        ("viewer", "owner", False),
        ("declarant", "viewer", True),
        ("declarant", "declarant", True),
        ("declarant", "owner", False),
        ("owner", "viewer", True),
        ("owner", "owner", True),
        ("nonsense", "viewer", False),
    ])
    def test_hierarchy(self, role, minimum, expected):
        assert is_at_least(role, minimum) is expected

    def test_valid_roles(self):
        assert all(is_valid_role(r) for r in ("viewer", "declarant", "owner"))
        assert not is_valid_role("admin")
        assert is_valid_role(DEV_ROLE)  # dev-режим использует реальную роль


class TestLastOwnerGuard:
    @pytest.mark.parametrize("current,new,owners,expected", [
        ("owner", None, 1, True),          # удаление единственного owner'а
        ("owner", "viewer", 1, True),      # понижение единственного owner'а
        ("owner", "owner", 1, False),      # no-op смена роли owner→owner
        ("owner", None, 2, False),         # есть второй owner
        ("owner", "declarant", 3, False),
        ("declarant", None, 1, False),     # не-owner не влияет
        ("viewer", "owner", 1, False),
    ])
    def test_matrix(self, current, new, owners, expected):
        assert would_lose_last_owner(current, new, owners) is expected


def test_normalize_email():
    assert normalize_email("  User@Example.COM ") == "user@example.com"
