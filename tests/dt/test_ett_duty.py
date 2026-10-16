"""Ставка ЕТТ в проекте ДТ: формула «какая сумма ниже», исключённый код, временные ставки."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from feap.dt.assembler import ItemRef, Rates, assemble
from feap.dt.duty_parser import DutyKind, parse_duty_rate
from feap.dt.flk import CodeInfo, draft_status, validate_draft
from feap.dt.payments import compute_duty
from feap.dt.schemas import PartyIn, ShipmentIn, ShipmentItemIn

CAR = ("15%, либо 12.5%, но не менее 0.6 EUR за 1 см3, мл, в зависимости от того, "
       "какая из исчисленных сумм таможенной пошлины ниже")
FX = {"EUR": Decimal("98.08")}
DAY = dt.date(2026, 10, 16)
RATES = Rates(rate_date=DAY, contract=Decimal("87.45"), usd=Decimal("87.45"), eur=Decimal("98.08"))


class Ctx:
    def code_info(self, code10: str) -> CodeInfo | None:
        return CodeInfo(exists=True, is_leaf=True)

    def classifier_codes(self, classifier: str) -> set[str] | None:
        return None

    def has_ntm_measures(self, code10: str) -> bool:
        return False


def test_alternative_rate_parsed():
    p = parse_duty_rate(CAR)
    assert p.kind is DutyKind.ALTERNATIVE_MIN and p.has_specific
    assert (p.alternative_pct, p.advalorem_pct, p.specific_amount, p.specific_currency) == (
        Decimal("15"), Decimal("12.5"), Decimal("0.6"), "EUR")


def _car(cv: str, cm3: str):
    return compute_duty(parse_duty_rate(CAR), customs_value=Decimal(cv), net_kg=None,
                        supp_qty=Decimal(cm3), fx=FX)


def test_alternative_takes_the_lower_sum():
    # 15% = 150 000; второй вариант = max(12,5% = 125 000; 0,6 € × 1500 см3 = 88 272) = 125 000.
    line = _car("1000000", "1500")
    assert line.amount == Decimal("125000.00") and "второй вариант" in line.note
    # 15% = 75 000; второй = max(62 500; 0,6 € × 2500 = 147 120) = 147 120 → ниже 15%.
    line = _car("500000", "2500")
    assert line.amount == Decimal("75000.00") and "15%" in line.note


def test_alternative_needs_engine_volume():
    line = compute_duty(parse_duty_rate(CAR), customs_value=Decimal("1000000"), net_kg=None,
                        supp_qty=None, fx=FX)
    assert line.amount is None and line.missing == ["объём в см3/мл (доп. единица)"]


def _shipment() -> ShipmentIn:
    return ShipmentIn(
        seller=PartyIn(name="Seller", country="CN"), buyer=PartyIn(name="Buyer", country="KG"),
        incoterms="CIF", contract_currency="USD",
        items=[ShipmentItemIn(description="Оксид титана пигментный, мешки 25 кг", hs_code="2823000000",
                              origin_country="CN", price=Decimal("10000"), net_kg=Decimal("1000"),
                              gross_kg=Decimal("1010"))],
    )


def test_removed_code_blocks_duty_instead_of_old_rate():
    ref = ItemRef(code_exists=True, is_leaf=True, duty_raw="5%", vat_rate="12%",
                  duty_issue="код 2823000000 отсутствует в действующей редакции ЕТТ",
                  duty_trace={"source": "fallback"})
    assembled = assemble(_shipment(), [ref], RATES)
    duty = assembled.graphs["items"][0]["g47"][0]
    assert duty["kind"] == "duty" and duty["amount"] is None
    assert any("отсутствует в действующей редакции ЕТТ" in m for m in assembled.missing_parameters)
    assert draft_status([], assembled.missing_parameters) == "needs_input"


def _note_trace(valid_to: str) -> dict:
    return {"source": "ett_note", "rate": "0%", "base_rate": "5%", "note_id": "123С",
            "valid_to": valid_to, "version": "2026-08-17",
            "from_decision": "Решения Совета Евразийской экономической комиссии от 9 июля 2026 г. № 76"}


def _findings(trace: dict) -> dict:
    ref = ItemRef(code_exists=True, is_leaf=True, duty_raw="0%", vat_rate="12%", duty_trace=trace)
    model = _shipment()
    graphs = assemble(model, [ref], RATES).graphs
    assert graphs["items"][0]["tariff"]["duty"] == trace
    return {f.rule: f for f in validate_draft(graphs, model, Ctx())}


def test_temporary_ett_rate_is_explained():
    rules = _findings(_note_trace("2028-10-31"))
    info = rules["I-ETT-NOTE"]
    assert info.severity == "info"
    assert "временная, по примечанию 123С) к ЕТТ" in info.message
    assert "действует до 31.10.2028" in info.message and "базовая ставка ЕТТ — 5%" in info.message
    assert "W-ETT-NOTE-END" not in rules


def test_temporary_ett_rate_ending_soon_warns():
    rules = _findings(_note_trace("2026-11-30"))
    warn = rules["W-ETT-NOTE-END"]
    assert warn.severity == "warning" and "по базовой ставке" in warn.message
    assert "I-ETT-NOTE" not in rules
