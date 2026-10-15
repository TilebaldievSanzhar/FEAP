"""Расчёт по товару без досье: временное досье, платежи, себестоимость ввоза."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from feap.calc.engine import (
    CALC_RULES,
    build_calc_out,
    landed_cost,
    select_findings,
    to_shipment,
)
from feap.calc.schemas import CalcIn, CostLine, PassportIn
from feap.dt.assembler import ItemRef, Rates, assemble
from feap.dt.flk import CodeInfo, validate_draft
from feap.dt.schemas import Finding, RemedyDecision
from feap.tariff.fees import kg_release_fee_rule
from feap.tariff.remedies import RemedyMeasure, RemedyRate

RATES = Rates(rate_date=dt.date(2026, 10, 14), contract=Decimal("87.45"),
              usd=Decimal("87.45"), eur=Decimal("97.9"))
COOK_REF = ItemRef(code_exists=True, is_leaf=True, duty_raw="9%", vat_rate="12%")
COOKWARE = RemedyMeasure(
    measure_no="AD-32", kind="antidumping", title="Алюминиевая посуда",
    countries=("CN",), codes=("7615101000",), status="applied",
    valid_from=dt.date(2021, 9, 25), valid_to=dt.date(2027, 5, 28),
    rates=(RemedyRate(Decimal("21.89")),),
)
FEE_RULE = kg_release_fee_rule(Decimal(100))


class FakeCtx:
    def code_info(self, code10: str) -> CodeInfo | None:
        return CodeInfo(exists=True, is_leaf=True)

    def classifier_codes(self, classifier: str) -> set[str] | None:
        return None

    def has_ntm_measures(self, code10: str) -> bool:
        return False


def calc_in(**over) -> CalcIn:
    base = dict(hs_code="7615101000", origin_country="cn", price=Decimal("10000"),
                currency="USD", incoterms="DAP", qty=Decimal("1000"), qty_unit="шт")
    base.update(over)
    return CalcIn(**base)


def run(calc: CalcIn, *, cost_rates=None):
    """То же, что run_calc, но со справочниками из теста вместо БД."""
    shipment = to_shipment(calc)
    assembled = assemble(shipment, [COOK_REF], RATES, fee_rule=FEE_RULE, remedies=[COOKWARE])
    findings = validate_draft(assembled.graphs, shipment, FakeCtx())
    return build_calc_out(calc, assembled, findings, contract_rate=RATES.contract,
                          cost_rates=cost_rates or {"KGS": Decimal(1)})


def test_to_shipment_single_item_with_placeholders():
    ship = to_shipment(calc_in(freight_to_border=Decimal("300"), manufacturer="Zhejiang Supor"))
    assert len(ship.items) == 1
    item = ship.items[0]
    assert item.hs_code == "7615101000" and item.origin_country == "CN"
    assert item.manufacturer == "Zhejiang Supor"
    assert ship.contract_currency == "USD" and ship.freight_to_border == Decimal("300")
    assert item.description  # временное досье требует описание


def test_undecided_remedy_is_needs_input_without_amounts():
    out = run(calc_in())
    assert out.status == "needs_input"
    assert any("remedy_decisions['AD-32']" in m for m in out.missing_parameters)
    assert out.landed_cost is None
    assert out.payments_total is not None  # пошлина/НДС/сбор посчитаны, меры — нет


def test_amounts_match_dt_draft_numbers():
    """Тот же пример, что e2e проекта ДТ: 10 000 USD × 87,45, AD-32 применяется."""
    out = run(calc_in(remedy_decisions={"AD-32": RemedyDecision(decision="applies")}))
    assert out.status == "ready", out.missing_parameters
    by_kind = {p.kind: p.amount for p in out.payments}
    assert out.customs_value == Decimal("874500.00")
    assert by_kind == {
        "duty": Decimal("78705.00"),
        "antidumping": Decimal("191428.05"),
        "vat": Decimal("114384.60"),
        "fee": Decimal("3498.00"),
    }
    assert out.payments_total == Decimal("388015.65")


def test_landed_cost_with_local_costs_and_per_unit():
    calc = calc_in(
        remedy_decisions={"AD-32": RemedyDecision(decision="applies")},
        costs=[CostLine(kind="broker", amount=Decimal("15000")),
               CostLine(kind="logistics", amount=Decimal("500"), currency="USD")],
    )
    out = run(calc, cost_rates={"KGS": Decimal(1), "USD": Decimal("87.45")})
    lc = out.landed_cost
    assert lc is not None
    # 874 500 товар + 388 015,65 платежи + 15 000 брокер + 43 725 доставка
    assert lc.total == Decimal("1321240.65")
    assert lc.vat == Decimal("114384.60")
    assert lc.total_without_vat == Decimal("1206856.05")
    assert lc.per_unit == Decimal("1321.24")
    assert lc.per_unit_without_vat == Decimal("1206.86")
    logistics = next(ln for ln in lc.lines if ln.kind == "logistics")
    assert logistics.amount == Decimal("43725.00") and logistics.source_currency == "USD"
    assert sum(ln.amount for ln in lc.lines) == lc.total


def test_freight_to_border_raises_customs_value_and_cost_once():
    calc = calc_in(incoterms="FOB", freight_to_border=Decimal("1000"),
                   remedy_decisions={"AD-32": RemedyDecision(decision="not_applicable")})
    out = run(calc)
    assert out.customs_value == Decimal("961950.00")      # (10 000 + 1 000) × 87,45
    additions = [ln for ln in out.landed_cost.lines if ln.kind == "additions"]
    assert [ln.amount for ln in additions] == [Decimal("87450.00")]


def test_freight_beyond_border_reduces_value_but_not_cost():
    calc = calc_in(freight_beyond_border=Decimal("500"),
                   remedy_decisions={"AD-32": RemedyDecision(decision="not_applicable")})
    out = run(calc)
    assert out.customs_value == Decimal("830775.00")      # (10 000 − 500) × 87,45
    goods = next(ln for ln in out.landed_cost.lines if ln.kind == "goods")
    assert goods.amount == Decimal("874500.00")            # оплачено по счёту целиком


def test_missing_cost_rate_blocks_landed_cost():
    out = build_calc_out(
        calc_in(remedy_decisions={"AD-32": RemedyDecision(decision="applies")},
                costs=[CostLine(amount=Decimal("100"), currency="CNY")]),
        assemble(to_shipment(calc_in()), [COOK_REF], RATES, fee_rule=FEE_RULE),
        [], contract_rate=RATES.contract, cost_rates={},
        extra_missing=["курс CNY НБКР на 2026-10-14 (расходы)"],
    )
    assert out.status == "needs_input" and out.landed_cost is None


def test_landed_cost_without_qty_has_no_per_unit():
    calc = calc_in(qty=None, remedy_decisions={"AD-32": RemedyDecision(decision="applies")})
    payments = run(calc).payments
    lc = landed_cost(calc, contract_rate=RATES.contract, payments=payments, cost_rates={})
    assert lc.per_unit is None and lc.total > 0


def test_findings_are_limited_to_calc_rules():
    findings = [
        Finding(rule="F-01", severity="error", message="нет отправителя"),
        Finding(rule="W-31", severity="warning", message="короткое описание"),
        Finding(rule="W-REMEDY-VAT", severity="warning", message="база НДС"),
        Finding(rule="W-FREIGHT", severity="warning", message="нет фрахта"),
    ]
    assert [f.rule for f in select_findings(findings)] == ["W-REMEDY-VAT", "W-FREIGHT"]
    assert "F-01" not in CALC_RULES


def test_placeholder_parties_do_not_raise_errors():
    out = run(calc_in(remedy_decisions={"AD-32": RemedyDecision(decision="applies")}))
    assert not [f for f in out.findings if f.severity == "error"], out.findings


def test_origin_must_be_iso2():
    with pytest.raises(ValidationError):
        calc_in(origin_country="China")


def test_passport_roundtrip_keeps_calc_params():
    passport = PassportIn.model_validate({
        "calc": {"origin_country": "cn", "price": "10000", "currency": "USD", "qty": "1000",
                 "costs": [{"kind": "broker", "amount": "15000"}],
                 "remedy_decisions": {"AD-32": {"decision": "applies"}}},
        "classification": {"source": "cascade", "confidence": 0.94,
                           "steps": [{"level": "position", "code": "7615", "name": "Изделия"}]},
    })
    stored = passport.model_dump(mode="json", exclude_none=True)
    again = PassportIn.model_validate(stored)
    assert again.calc.origin_country == "CN"
    assert again.calc.costs[0].amount == Decimal("15000")
    assert again.classification.steps[0].code == "7615"


def test_lot_price_from_unit_price_and_qty():
    calc = CalcIn(hs_code="7615101000", unit_price=Decimal("38.50"), qty=Decimal("100"),
                  currency="CNY")
    assert calc.price == Decimal("3850.00")
    explicit = CalcIn(hs_code="7615101000", price=Decimal("4000"), unit_price=Decimal("38.50"),
                      qty=Decimal("100"))
    assert explicit.price == Decimal("4000")          # стоимость по счёту важнее


def test_lot_price_required():
    with pytest.raises(ValidationError):
        CalcIn(hs_code="7615101000", unit_price=Decimal("38.50"))   # нет количества
    with pytest.raises(ValidationError):
        CalcIn(hs_code="7615101000")
