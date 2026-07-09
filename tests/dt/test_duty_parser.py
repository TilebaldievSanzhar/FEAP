"""Парсер ставок ЕТТ: репрезентативные форматы + смоук по всему корпусу leaves.json."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from feap.dt.duty_parser import (
    BasisKind,
    DutyKind,
    DutyParseError,
    parse_duty_rate,
)

LEAVES_PATH = Path(__file__).resolve().parents[2] / "data" / "parsed" / "leaves.json"


@pytest.mark.parametrize(
    ("raw", "kind", "pct", "amount", "currency", "basis"),
    [
        ("5%", DutyKind.AD_VALOREM, "5", None, None, None),
        ("12.5%", DutyKind.AD_VALOREM, "12.5", None, None, None),
        ("0%", DutyKind.AD_VALOREM, "0", None, None, None),
        (
            "25%, но не менее 0.2 EUR за 1 кг",
            DutyKind.COMBINED_MIN, "25", "0.2", "EUR", BasisKind.NET_KG,
        ),
        (
            "10%, но не менее 0.046 EUR за 1 л",
            DutyKind.COMBINED_MIN, "10", "0.046", "EUR", BasisKind.LITRE,
        ),
        (
            "20%, но не менее 0.8 EUR за 1 см3, мл",
            DutyKind.COMBINED_MIN, "20", "0.8", "EUR", BasisKind.CM3,
        ),
        ("1.75 EUR за 1 кг", DutyKind.SPECIFIC, None, "1.75", "EUR", BasisKind.NET_KG),
        ("250 USD за 1 т", DutyKind.SPECIFIC, None, "250", "USD", BasisKind.NET_TON),
        ("1.25 EUR за 1 пар", DutyKind.SPECIFIC, None, "1.25", "EUR", BasisKind.PAIR),
        ("0.38 EUR за 1 м2", DutyKind.SPECIFIC, None, "0.38", "EUR", BasisKind.M2),
        (
            "10% плюс 0.08 EUR за 1 кг",
            DutyKind.COMBINED_PLUS, "10", "0.08", "EUR", BasisKind.NET_KG,
        ),
    ],
)
def test_representative_formats(raw, kind, pct, amount, currency, basis):
    parsed = parse_duty_rate(raw)
    assert parsed.kind is kind
    assert parsed.advalorem_pct == (Decimal(pct) if pct else None)
    assert parsed.specific_amount == (Decimal(amount) if amount else None)
    assert parsed.specific_currency == currency
    assert parsed.basis == basis


def test_empty_and_none_are_none_kind():
    assert parse_duty_rate("").kind is DutyKind.NONE
    assert parse_duty_rate(None).kind is DutyKind.NONE
    assert parse_duty_rate("   ").kind is DutyKind.NONE


def test_thousand_pieces_unit():
    parsed = parse_duty_rate("2 EUR за 1 1000 шт")
    assert parsed.basis is BasisKind.THOUSAND_PIECES


def test_whitespace_normalization():
    parsed = parse_duty_rate("10%,  но не менее  1.5 EUR  за 1 кг")
    assert parsed.kind is DutyKind.COMBINED_MIN
    assert parsed.specific_amount == Decimal("1.5")


@pytest.mark.parametrize("raw", ["abc", "10", "10% минус 1 EUR за 1 кг", "5 GBP за 1 кг"])
def test_unknown_formats_raise(raw):
    with pytest.raises(DutyParseError):
        parse_duty_rate(raw)


@pytest.mark.skipif(not LEAVES_PATH.exists(), reason="leaves.json не собран")
def test_full_corpus_smoke():
    """Каждая ставка реального корпуса должна разбираться без исключений."""
    leaves = json.loads(LEAVES_PATH.read_text(encoding="utf-8"))
    failures: list[str] = []
    kinds: dict[DutyKind, int] = {}
    for leaf in leaves:
        raw = leaf.get("duty_rate") or ""
        try:
            parsed = parse_duty_rate(raw)
            kinds[parsed.kind] = kinds.get(parsed.kind, 0) + 1
        except DutyParseError:
            failures.append(raw)
    assert not failures, f"Неразобранные ставки ({len(failures)}): {sorted(set(failures))[:10]}"
    # Санити: в корпусе есть все основные виды ставок.
    assert kinds.get(DutyKind.AD_VALOREM, 0) > 10_000
    assert kinds.get(DutyKind.COMBINED_MIN, 0) > 500
    assert kinds.get(DutyKind.SPECIFIC, 0) > 300
    assert kinds.get(DutyKind.COMBINED_PLUS, 0) > 10
