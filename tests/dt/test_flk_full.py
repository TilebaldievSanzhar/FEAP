"""Полный ФЛК (Фаза 3): F-09 классификаторы, F-10 арифметика 47,
F-13 преференции, F-14 НТР, F-15 даты, W-TULPAR."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from feap.dt.assembler import DocEntry, ItemRef, Rates, assemble
from feap.dt.flk import CodeInfo, validate_draft
from feap.dt.schemas import PartyIn, ShipmentIn, ShipmentItemIn

from .test_assembler_flk import FakeCtx

RATES = Rates(rate_date=dt.date(2026, 7, 4), contract=Decimal("87.5"),
              usd=Decimal("87.5"), eur=Decimal("95"))

CLASSIFIERS = {
    "delivery_terms": {"EXW", "FCA", "FOB", "CIF", "DAP"},
    "transport_types": {"20", "30", "40"},
    "deal_nature": {"010"},
    "customs_procedures": {"40", "00", "10"},
    "doc_kinds": {"04021", "03011", "02015", "06011"},
    "payment_kinds": {"1010", "2010", "4010", "5010"},
}


def _model(**overrides) -> ShipmentIn:
    base = dict(
        seller=PartyIn(name="Alpha Trading", country="CN"),
        buyer=PartyIn(name="ОсОО «Бета»", inn="01234567890123"),
        incoterms="CIF",
        contract_currency="USD",
        deal_nature="010",
        transport_at_border_type="30",
        items=[ShipmentItemIn(
            description="Электрическая зубная щётка бытовая, пластиковый корпус",
            hs_code="8509800000",
            price=Decimal("1000"), net_kg=Decimal("90"), gross_kg=Decimal("100"),
        )],
    )
    base.update(overrides)
    return ShipmentIn(**base)


ITEM_REFS = [ItemRef(code_exists=True, is_leaf=True, duty_raw="10%", vat_rate="12%")]


def _ctx(**kw) -> FakeCtx:
    defaults = dict(
        codes={"8509800000": CodeInfo(exists=True, is_leaf=True)},
        classifiers=CLASSIFIERS,
    )
    defaults.update(kw)
    return FakeCtx(**defaults)


def _rules(findings, rule, severity=None):
    return [f for f in findings
            if f.rule == rule and (severity is None or f.severity == severity)]


class TestF09Classifiers:
    def test_happy_path_no_f09_errors(self):
        model = _model()
        result = assemble(model, ITEM_REFS, RATES,
                          documents=[DocEntry(kind="invoice", code="04021")])
        findings = validate_draft(result.graphs, model, _ctx())
        assert _rules(findings, "F-09", "error") == []
        assert _rules(findings, "F-09", "info") == []   # всё загружено — без skip

    def test_tampered_incoterms_flagged(self):
        model = _model()
        result = assemble(model, ITEM_REFS, RATES)
        result.graphs["header"]["g20"]["incoterms"] = "XXX"
        findings = validate_draft(result.graphs, model, _ctx())
        assert any("XXX" in f.message for f in _rules(findings, "F-09", "error"))

    def test_unknown_deal_nature_flagged(self):
        model = _model(deal_nature="999")
        result = assemble(model, ITEM_REFS, RATES)
        findings = validate_draft(result.graphs, model, _ctx())
        assert _rules(findings, "F-09", "error")

    def test_unknown_doc_code_flagged(self):
        model = _model()
        result = assemble(model, ITEM_REFS, RATES,
                          documents=[DocEntry(kind="invoice", code="99999")])
        findings = validate_draft(result.graphs, model, _ctx())
        assert any("99999" in f.message for f in _rules(findings, "F-09", "error"))

    def test_missing_classifiers_single_info(self):
        model = _model()
        result = assemble(model, ITEM_REFS, RATES)
        findings = validate_draft(result.graphs, model, _ctx(classifiers={}))
        infos = _rules(findings, "F-09", "info")
        assert len(infos) == 1
        assert "не загружены" in infos[0].message
        assert _rules(findings, "F-09", "error") == []


class TestF10Arithmetic:
    def test_self_consistent_no_findings(self):
        model = _model()
        result = assemble(model, ITEM_REFS, RATES)
        findings = validate_draft(result.graphs, model, _ctx())
        assert _rules(findings, "F-10") == []

    def test_tampered_duty_amount(self):
        model = _model()
        result = assemble(model, ITEM_REFS, RATES)
        duty = next(p for p in result.graphs["items"][0]["g47"] if p["kind"] == "duty")
        duty["amount"] = "1.00"
        findings = validate_draft(result.graphs, model, _ctx())
        assert _rules(findings, "F-10", "error")

    def test_tampered_vat_base(self):
        model = _model()
        result = assemble(model, ITEM_REFS, RATES)
        vat = next(p for p in result.graphs["items"][0]["g47"] if p["kind"] == "vat")
        vat["basis"] = "1.00"
        findings = validate_draft(result.graphs, model, _ctx())
        messages = " | ".join(f.message for f in _rules(findings, "F-10", "error"))
        assert "база НДС" in messages

    def test_tampered_totals(self):
        model = _model()
        result = assemble(model, ITEM_REFS, RATES)
        result.graphs["totals"]["payments"]["duty"] = "999999.00"
        findings = validate_draft(result.graphs, model, _ctx())
        assert any("итогом" in f.message for f in _rules(findings, "F-10", "error"))


class TestF13Preference:
    def test_preference_without_document(self):
        model = _model()
        model.items[0].preference = "ПППП"
        result = assemble(model, ITEM_REFS, RATES)
        findings = validate_draft(result.graphs, model, _ctx())
        assert _rules(findings, "F-13", "error")

    def test_preference_with_cert_origin_ok(self):
        model = _model()
        model.items[0].preference = "ПППП"
        result = assemble(model, ITEM_REFS, RATES,
                          documents=[DocEntry(kind="cert_origin", code="06011",
                                              number="СТ-1 №1")])
        findings = validate_draft(result.graphs, model, _ctx())
        assert _rules(findings, "F-13") == []

    def test_no_preference_values_ignored(self):
        for value in (None, "0000", "ОООО"):
            model = _model()
            model.items[0].preference = value
            result = assemble(model, ITEM_REFS, RATES)
            findings = validate_draft(result.graphs, model, _ctx())
            assert _rules(findings, "F-13") == [], value


class TestF14Ntm:
    def test_ntm_code_without_permit_warns(self):
        model = _model()
        result = assemble(model, ITEM_REFS, RATES)
        findings = validate_draft(
            result.graphs, model, _ctx(ntm_codes={"8509800000"})
        )
        warns = _rules(findings, "F-14", "warning")
        assert warns and "нетарифного" in warns[0].message

    def test_permit_attached_no_warning(self):
        model = _model()
        result = assemble(model, ITEM_REFS, RATES,
                          documents=[DocEntry(kind="permit", number="P-1")])
        findings = validate_draft(
            result.graphs, model, _ctx(ntm_codes={"8509800000"})
        )
        assert _rules(findings, "F-14") == []

    def test_no_ntm_no_warning(self):
        model = _model()
        result = assemble(model, ITEM_REFS, RATES)
        findings = validate_draft(result.graphs, model, _ctx())
        assert _rules(findings, "F-14") == []


class TestF15Dates:
    def test_future_document_date_warns(self):
        model = _model()
        result = assemble(model, ITEM_REFS, RATES,
                          documents=[DocEntry(kind="invoice", code="04021",
                                              number="INV-1", date="2099-01-01")])
        findings = validate_draft(result.graphs, model, _ctx())
        assert any("позже даты досье" in f.message
                   for f in _rules(findings, "F-15", "warning"))

    def test_contract_after_invoice_warns(self):
        model = _model()
        docs = [
            DocEntry(kind="invoice", code="04021", number="INV-1", date="2026-06-01"),
            DocEntry(kind="contract", code="03011", number="К-1", date="2026-06-15"),
        ]
        result = assemble(model, ITEM_REFS, RATES, documents=docs)
        findings = validate_draft(result.graphs, model, _ctx())
        assert any("позже инвойса" in f.message
                   for f in _rules(findings, "F-15", "warning"))

    def test_correct_order_silent(self):
        model = _model()
        docs = [
            DocEntry(kind="contract", code="03011", number="К-1", date="2026-05-01"),
            DocEntry(kind="invoice", code="04021", number="INV-1", date="2026-06-01"),
        ]
        result = assemble(model, ITEM_REFS, RATES, documents=docs)
        findings = validate_draft(result.graphs, model, _ctx())
        assert _rules(findings, "F-15") == []


class TestWTulpar:
    def test_bad_permit_status_warns(self):
        model = _model()
        result = assemble(model, ITEM_REFS, RATES,
                          documents=[DocEntry(kind="permit", number="P-1",
                                              permit_status="not_found")])
        findings = validate_draft(result.graphs, model, _ctx())
        assert _rules(findings, "W-TULPAR", "warning")

    def test_found_and_unchecked_silent(self):
        model = _model()
        docs = [
            DocEntry(kind="permit", number="P-1", permit_status="found"),
            DocEntry(kind="permit", number="P-2"),
            DocEntry(kind="permit", number="P-3", permit_status="format_ok"),
        ]
        result = assemble(model, ITEM_REFS, RATES, documents=docs)
        findings = validate_draft(result.graphs, model, _ctx())
        assert _rules(findings, "W-TULPAR") == []
