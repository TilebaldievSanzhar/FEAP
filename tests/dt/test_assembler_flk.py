"""assembler + flk: сквозная сборка графов и самосогласованность ФЛК."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from feap.dt.assembler import ItemRef, Rates, assemble, sheets_count
from feap.dt.flk import CodeInfo, draft_status, validate_draft
from feap.dt.schemas import PartyIn, ShipmentIn, ShipmentItemIn

RATES = Rates(
    rate_date=dt.date(2026, 7, 4),
    contract=Decimal("87.5"),   # USD
    usd=Decimal("87.5"),
    eur=Decimal("95"),
)


class FakeCtx:
    """RefContext для тестов: словарь код → CodeInfo (+ классификаторы/НТР)."""

    def __init__(
        self,
        codes: dict[str, CodeInfo],
        classifiers: dict[str, set[str]] | None = None,
        ntm_codes: set[str] | None = None,
    ):
        self._codes = codes
        self._classifiers = classifiers or {}
        self._ntm = ntm_codes or set()

    def code_info(self, code10: str) -> CodeInfo | None:
        return self._codes.get(code10)

    def classifier_codes(self, classifier: str) -> set[str] | None:
        return self._classifiers.get(classifier)

    def has_ntm_measures(self, code10: str) -> bool:
        return code10 in self._ntm


def make_shipment(**overrides) -> ShipmentIn:
    base = dict(
        seller=PartyIn(name="Guangzhou Trading Co., Ltd", country="CN"),
        buyer=PartyIn(name="ОсОО «Импортер»", country="KG", inn="01234567890123",
                      address="г. Бишкек, ул. Киевская 1"),
        incoterms="EXW",
        incoterms_place="Guangzhou",
        contract_currency="USD",
        freight_to_border=Decimal("400"),
        trading_country="CN",
        dispatch_country="CN",
        items=[
            ShipmentItemIn(
                description="Электрическая зубная щётка со встроенным аккумулятором, "
                            "пластиковый корпус, бытовая, торговая марка X",
                hs_code="8509800000",
                origin_country="CN",
                price=Decimal("1000"),
                net_kg=Decimal("90"),
                gross_kg=Decimal("100"),
                packages=10,
            ),
            ShipmentItemIn(
                description="Обувь женская из натуральной кожи на резиновой подошве, "
                            "летняя, размеры 36-41",
                hs_code="6403990000",
                origin_country="CN",
                price=Decimal("3000"),
                net_kg=Decimal("270"),
                gross_kg=Decimal("300"),
                packages=30,
                supp_qty=Decimal("500"),
                supp_unit="пар",
            ),
        ],
    )
    base.update(overrides)
    return ShipmentIn(**base)


ITEM_REFS = [
    ItemRef(code_exists=True, is_leaf=True, duty_raw="10%", vat_rate="12%"),
    ItemRef(code_exists=True, is_leaf=True,
            duty_raw="1.25 EUR за 1 пар", vat_rate="12%", unit_secondary="пар"),
]

CTX = FakeCtx({
    "8509800000": CodeInfo(exists=True, is_leaf=True),
    "6403990000": CodeInfo(exists=True, is_leaf=True, unit_secondary="пар"),
})


def test_sheets_count():
    assert sheets_count(0) == 1
    assert sheets_count(1) == 1
    assert sheets_count(2) == 2
    assert sheets_count(4) == 2
    assert sheets_count(5) == 3
    assert sheets_count(999) == 1 + (998 + 2) // 3


class TestAssembleHappyPath:
    def setup_method(self):
        self.shipment = make_shipment()
        self.result = assemble(self.shipment, ITEM_REFS, RATES, fee_kgs=Decimal("2000"))

    def test_no_missing_parameters(self):
        assert self.result.missing_parameters == []

    def test_header_totals_consistent(self):
        g = self.result.graphs
        header, items, totals = g["header"], g["items"], g["totals"]
        assert header["g05"] == 2
        assert header["g03"]["sheets"] == 2
        assert header["g22"] == {"currency": "USD", "amount": "4000.00"}
        # Σ графы 45 = графа 12
        cv_sum = sum(Decimal(i["g45"]) for i in items)
        assert str(cv_sum) == header["g12"] == totals["g12"]

    def test_customs_values_with_freight(self):
        items = self.result.graphs["items"]
        # цены: 87 500 + 262 500; фрахт 400×87.5=35 000 по брутто 100/300 → 8 750 / 26 250
        assert items[0]["g45"] == "96250.00"
        assert items[1]["g45"] == "288750.00"

    def test_duty_advalorem_and_specific(self):
        items = self.result.graphs["items"]
        duty0 = next(p for p in items[0]["g47"] if p["kind"] == "duty")
        duty1 = next(p for p in items[1]["g47"] if p["kind"] == "duty")
        assert Decimal(duty0["amount"]) == Decimal("9625.00")       # 10% от 96 250
        assert Decimal(duty1["amount"]) == Decimal("59375.00")      # 500 пар × 1.25 EUR × 95

    def test_vat_base(self):
        items = self.result.graphs["items"]
        vat0 = next(p for p in items[0]["g47"] if p["kind"] == "vat")
        assert Decimal(vat0["basis"]) == Decimal("105875.00")       # 96 250 + 9 625
        assert Decimal(vat0["amount"]) == Decimal("12705.00")

    def test_statistical_value(self):
        items = self.result.graphs["items"]
        assert items[0]["g46"] == "1100.00"  # 96 250 / 87.5

    def test_fee_in_totals(self):
        totals = self.result.graphs["totals"]
        assert totals["payments"]["fee"] == "2000.00"
        assert totals["fee_line"]["kind"] == "fee"

    def test_flk_self_consistent(self):
        findings = validate_draft(self.result.graphs, self.shipment, CTX)
        errors = [f for f in findings if f.severity == "error"]
        assert errors == [], [f.model_dump() for f in errors]
        assert draft_status(findings, self.result.missing_parameters) == "ready"


class TestMissingParameters:
    def test_combined_rate_without_net_kg_stops(self):
        shipment = make_shipment()
        shipment.items[0].net_kg = None
        refs = [
            ItemRef(code_exists=True, is_leaf=True,
                    duty_raw="25%, но не менее 0.2 EUR за 1 кг", vat_rate="12%"),
            ITEM_REFS[1],
        ]
        result = assemble(shipment, refs, RATES)
        assert any("вес нетто" in m for m in result.missing_parameters)
        findings = validate_draft(result.graphs, shipment, CTX)
        assert draft_status(findings, result.missing_parameters) == "needs_input"

    def test_missing_contract_rate_reported(self):
        rates = Rates(rate_date=dt.date(2026, 7, 4), contract=None, usd=None, eur=None)
        result = assemble(make_shipment(), ITEM_REFS, rates)
        assert any("курс USD" in m for m in result.missing_parameters)
        assert any("графа 23" in m for m in result.missing_parameters)


class TestFlkRules:
    def test_unknown_code_is_error(self):
        shipment = make_shipment()
        result = assemble(shipment, ITEM_REFS, RATES)
        ctx = FakeCtx({"6403990000": CodeInfo(exists=True, is_leaf=True, unit_secondary="пар")})
        findings = validate_draft(result.graphs, shipment, ctx)
        assert any(f.rule == "F-03" and "8509800000" in f.message for f in findings)

    def test_non_leaf_code_is_error(self):
        shipment = make_shipment()
        result = assemble(shipment, ITEM_REFS, RATES)
        ctx = FakeCtx({
            "8509800000": CodeInfo(exists=True, is_leaf=False),
            "6403990000": CodeInfo(exists=True, is_leaf=True, unit_secondary="пар"),
        })
        findings = validate_draft(result.graphs, shipment, ctx)
        assert any(f.rule == "F-03" and "конечным" in f.message for f in findings)

    def test_netto_greater_than_brutto(self):
        shipment = make_shipment()
        shipment.items[0].net_kg = Decimal("150")   # > брутто 100
        result = assemble(shipment, ITEM_REFS, RATES)
        findings = validate_draft(result.graphs, shipment, CTX)
        assert any(f.rule == "F-06" for f in findings)

    def test_required_supp_unit_missing(self):
        shipment = make_shipment()
        shipment.items[1].supp_qty = None
        result = assemble(shipment, ITEM_REFS, RATES)
        findings = validate_draft(result.graphs, shipment, CTX)
        assert any(f.rule == "F-07" for f in findings)

    def test_short_description_warning(self):
        shipment = make_shipment()
        shipment.items[0].description = "щётка"
        result = assemble(shipment, ITEM_REFS, RATES)
        findings = validate_draft(result.graphs, shipment, CTX)
        w = [f for f in findings if f.rule == "W-31"]
        assert w and w[0].severity == "warning"

    def test_exw_zero_freight_warning(self):
        shipment = make_shipment(freight_to_border=Decimal(0))
        result = assemble(shipment, ITEM_REFS, RATES)
        findings = validate_draft(result.graphs, shipment, CTX)
        assert any(f.rule == "W-FREIGHT" for f in findings)

    def test_invoice_total_mismatch(self):
        shipment = make_shipment()
        result = assemble(shipment, ITEM_REFS, RATES)
        # Ломаем графу 22 руками — имитация ручной правки
        result.graphs["header"]["g22"]["amount"] = "9999.00"
        findings = validate_draft(result.graphs, shipment, CTX)
        assert any(f.rule == "F-04" for f in findings)

    def test_bad_inn_warning(self):
        shipment = make_shipment(
            buyer=PartyIn(name="ОсОО «Импортер»", country="KG", inn="123"),
        )
        result = assemble(shipment, ITEM_REFS, RATES)
        findings = validate_draft(result.graphs, shipment, CTX)
        assert any(f.rule == "F-11" for f in findings)

    def test_no_items_is_error(self):
        shipment = make_shipment(items=[])
        result = assemble(shipment, [], RATES)
        findings = validate_draft(result.graphs, shipment, CTX)
        assert any(f.rule == "F-01" for f in findings)
        assert draft_status(findings, result.missing_parameters) == "has_errors"
