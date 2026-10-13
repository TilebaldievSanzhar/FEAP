"""Сбор за таможенные операции КР: 0,4% от ТС, не менее 5 и не более 2500 РП."""

from __future__ import annotations

from decimal import Decimal

from feap.tariff.fees import compute_percent_fee, kg_release_fee_rule

RULE = kg_release_fee_rule()


def test_percent_in_range():
    res = compute_percent_fee(Decimal("1000000"), RULE)
    assert res.amount == Decimal("4000.00")
    assert res.bound is None
    assert res.basis == Decimal("1000000.00")


def test_minimum_five_calc_indices():
    res = compute_percent_fee(Decimal("50000"), RULE)   # 0,4% = 200 < 500
    assert res.amount == Decimal("500.00")
    assert res.bound == "min"


def test_maximum_2500_calc_indices():
    res = compute_percent_fee(Decimal("100000000"), RULE)   # 0,4% = 400 000 > 250 000
    assert res.amount == Decimal("250000.00")
    assert res.bound == "max"


def test_calc_index_is_configurable():
    rule = kg_release_fee_rule(Decimal("120"))
    assert rule.min_kgs == Decimal("600")
    assert compute_percent_fee(Decimal("1000"), rule).amount == Decimal("600.00")


def test_rate_text_is_human_readable():
    text = compute_percent_fee(Decimal("1000000"), RULE).rate_text
    assert "0.4%" in text and "500" in text and "250000" in text
    assert "E+" not in text
