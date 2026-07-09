"""customs_value: распределение расходов и графы 45/46."""

from __future__ import annotations

from decimal import Decimal

import pytest

from feap.dt.customs_value import (
    allocate,
    compute_customs_values,
    incoterms_profile,
    quantize_money,
    statistical_value_usd,
)


class TestAllocate:
    def test_sum_is_exact(self):
        shares = allocate(Decimal("100.00"), [Decimal(1), Decimal(1), Decimal(1)])
        assert sum(shares) == Decimal("100.00")
        assert all(s >= 0 for s in shares)

    def test_proportional(self):
        shares = allocate(Decimal("100.00"), [Decimal(3), Decimal(1)])
        assert shares == [Decimal("75.00"), Decimal("25.00")]

    def test_rounding_remainder_distributed(self):
        # 100 / 3 = 33.333... — копейка уходит одной из долей, сумма сходится
        shares = allocate(Decimal("100.00"), [Decimal(1)] * 3)
        assert sum(shares) == Decimal("100.00")
        assert sorted(shares) == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]

    def test_zero_weights_fall_back_to_equal(self):
        shares = allocate(Decimal("10.00"), [Decimal(0), Decimal(0)])
        assert sum(shares) == Decimal("10.00")
        assert shares == [Decimal("5.00"), Decimal("5.00")]

    def test_empty(self):
        assert allocate(Decimal("10.00"), []) == []


class TestComputeCustomsValues:
    def test_exw_adds_freight_by_weight(self):
        result = compute_customs_values(
            prices=[Decimal(1000), Decimal(3000)],
            contract_rate=Decimal("87.5"),
            freight_to_border=Decimal(400),
            insurance=Decimal(0),
            allocation_method="gross_weight",
            gross_weights=[Decimal(100), Decimal(300)],
        )
        # Цены: 87 500 и 262 500; фрахт 400 × 87.5 = 35 000 → 25/75
        assert result.prices_kgs == [Decimal("87500.00"), Decimal("262500.00")]
        assert result.additions_total == Decimal("35000.00")
        assert result.customs_values == [Decimal("96250.00"), Decimal("288750.00")]

    def test_deduction_reduces_value(self):
        result = compute_customs_values(
            prices=[Decimal(1000)],
            contract_rate=Decimal(80),
            freight_beyond_border=Decimal(100),
        )
        assert result.customs_values == [Decimal("72000.00")]

    def test_value_allocation_method(self):
        result = compute_customs_values(
            prices=[Decimal(100), Decimal(300)],
            contract_rate=Decimal(1),
            freight_to_border=Decimal(40),
            allocation_method="value",
        )
        assert result.customs_values == [Decimal("110.00"), Decimal("330.00")]

    def test_missing_weights_fall_back_to_value_basis(self):
        result = compute_customs_values(
            prices=[Decimal(100), Decimal(100)],
            contract_rate=Decimal(1),
            freight_to_border=Decimal(10),
            allocation_method="gross_weight",
            gross_weights=[None, None],
        )
        assert result.customs_values == [Decimal("105.00"), Decimal("105.00")]


def test_statistical_value_usd():
    assert statistical_value_usd(Decimal("87500.00"), Decimal("87.5")) == Decimal("1000.00")
    with pytest.raises(ValueError):
        statistical_value_usd(Decimal(1), Decimal(0))


def test_incoterms_profiles():
    assert incoterms_profile("EXW").freight_in_price is False
    assert incoterms_profile("CIF").freight_in_price is True
    assert incoterms_profile("CIF").insurance_in_price is True
    assert incoterms_profile("DAP").insurance_in_price is False


def test_quantize_money_half_up():
    assert quantize_money(Decimal("1.005")) == Decimal("1.01")
    assert quantize_money(Decimal("1.004")) == Decimal("1.00")
