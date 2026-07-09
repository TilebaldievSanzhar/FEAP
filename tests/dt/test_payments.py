"""payments: графа 47 — пошлина всех видов, акциз, НДС, сбор, missing-механика."""

from __future__ import annotations

from decimal import Decimal

from feap.dt.duty_parser import parse_duty_rate
from feap.dt.payments import (
    compute_duty,
    compute_excise,
    compute_fee,
    compute_vat,
    parse_pct,
)

FX = {"EUR": Decimal("95"), "USD": Decimal("87.5")}


class TestDuty:
    def test_ad_valorem(self):
        line = compute_duty(
            parse_duty_rate("10%"),
            customs_value=Decimal("100000.00"), net_kg=None, supp_qty=None, fx=FX,
        )
        assert line.amount == Decimal("10000.00")
        assert not line.missing

    def test_specific_per_kg(self):
        line = compute_duty(
            parse_duty_rate("1.75 EUR за 1 кг"),
            customs_value=Decimal("100000.00"), net_kg=Decimal("200"), supp_qty=None, fx=FX,
        )
        assert line.amount == Decimal("33250.00")  # 200 × 1.75 × 95

    def test_specific_per_ton(self):
        line = compute_duty(
            parse_duty_rate("250 USD за 1 т"),
            customs_value=Decimal(0), net_kg=Decimal("2000"), supp_qty=None, fx=FX,
        )
        assert line.amount == Decimal("43750.00")  # 2 т × 250 × 87.5

    def test_specific_per_thousand_pieces(self):
        line = compute_duty(
            parse_duty_rate("2 EUR за 1 1000 шт"),
            customs_value=Decimal(0), net_kg=None, supp_qty=Decimal("5000"), fx=FX,
        )
        assert line.amount == Decimal("950.00")  # 5 × 2 × 95

    def test_combined_min_advalorem_wins(self):
        line = compute_duty(
            parse_duty_rate("25%, но не менее 0.2 EUR за 1 кг"),
            customs_value=Decimal("100000.00"), net_kg=Decimal("100"), supp_qty=None, fx=FX,
        )
        # адвалорная 25 000 > специфическая 1 900
        assert line.amount == Decimal("25000.00")
        assert "адвалорная" in (line.note or "")

    def test_combined_min_specific_wins(self):
        line = compute_duty(
            parse_duty_rate("10%, но не менее 1.88 EUR за 1 кг"),
            customs_value=Decimal("1000.00"), net_kg=Decimal("1000"), supp_qty=None, fx=FX,
        )
        # специфическая 1000 × 1.88 × 95 = 178 600 > адвалорной 100
        assert line.amount == Decimal("178600.00")

    def test_combined_plus_sums(self):
        line = compute_duty(
            parse_duty_rate("10% плюс 0.08 EUR за 1 кг"),
            customs_value=Decimal("10000.00"), net_kg=Decimal("50"), supp_qty=None, fx=FX,
        )
        assert line.amount == Decimal("1380.00")  # 1000 + 50×0.08×95=380

    def test_missing_net_kg_stops_not_zeroes(self):
        line = compute_duty(
            parse_duty_rate("25%, но не менее 0.2 EUR за 1 кг"),
            customs_value=Decimal("100000.00"), net_kg=None, supp_qty=None, fx=FX,
        )
        assert line.amount is None
        assert any("вес нетто" in m for m in line.missing)

    def test_missing_fx_rate(self):
        line = compute_duty(
            parse_duty_rate("1.75 EUR за 1 кг"),
            customs_value=Decimal(0), net_kg=Decimal(10), supp_qty=None, fx={},
        )
        assert line.amount is None
        assert any("курс EUR" in m for m in line.missing)

    def test_none_kind_zero(self):
        line = compute_duty(
            parse_duty_rate(""),
            customs_value=Decimal("1000.00"), net_kg=None, supp_qty=None, fx=FX,
        )
        assert line.amount == Decimal("0.00")


class TestExcise:
    def test_per_litre(self):
        line = compute_excise(
            rate_amount=Decimal("100"), rate_unit="сом/литр",
            net_kg=None, supp_qty=Decimal("50"),
        )
        assert line.amount == Decimal("5000.00")

    def test_missing_supp_qty(self):
        line = compute_excise(
            rate_amount=Decimal("100"), rate_unit="сом/литр",
            net_kg=Decimal("10"), supp_qty=None,
        )
        assert line.amount is None
        assert line.missing

    def test_unknown_unit(self):
        line = compute_excise(
            rate_amount=Decimal("1"), rate_unit="сом/градус",
            net_kg=None, supp_qty=Decimal(1),
        )
        assert line.amount is None
        assert "неподдерживаемая" in line.missing[0]


class TestVat:
    def test_base_includes_duty_and_excise(self):
        line = compute_vat(
            customs_value=Decimal("100000.00"),
            duty_amount=Decimal("10000.00"),
            excise_amount=Decimal("5000.00"),
            vat_rate_str="12%",
        )
        assert line.basis == Decimal("115000.00")
        assert line.amount == Decimal("13800.00")

    def test_exemption_zero_rate(self):
        line = compute_vat(
            customs_value=Decimal("100000.00"),
            duty_amount=Decimal("0.00"), excise_amount=Decimal(0),
            vat_rate_str="0%", exemption_note="НК КР ст. 289",
        )
        assert line.amount == Decimal("0.00")
        assert line.note == "НК КР ст. 289"

    def test_pending_duty_blocks_vat(self):
        line = compute_vat(
            customs_value=Decimal("1000.00"), duty_amount=None,
            excise_amount=Decimal(0), vat_rate_str="12%",
        )
        assert line.amount is None
        assert any("пошлины" in m for m in line.missing)

    def test_pending_excise_blocks_vat(self):
        line = compute_vat(
            customs_value=Decimal("1000.00"), duty_amount=Decimal("100.00"),
            excise_amount=Decimal(0), vat_rate_str="12%", excise_pending=True,
        )
        assert line.amount is None
        assert any("акциза" in m for m in line.missing)


def test_parse_pct():
    assert parse_pct("12%") == Decimal("12")
    assert parse_pct(" 6.5% ") == Decimal("6.5")
    assert parse_pct("освобождено") is None
    assert parse_pct(None) is None


def test_fee():
    assert compute_fee(None) is None
    assert compute_fee(Decimal(0)) is None
    line = compute_fee(Decimal("2000"))
    assert line is not None and line.amount == Decimal("2000.00")
