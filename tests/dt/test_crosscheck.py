"""Кросс-сверка: числовые X-правила (код) и harness LLM-сопоставления."""

from __future__ import annotations

from decimal import Decimal

from feap.dt import crosscheck
from feap.dt.crosscheck import (
    EntityMatchResult,
    LineMatch,
    PartyMatch,
    build_line_inputs,
    build_party_pairs,
    match_entities,
    numeric_findings,
)
from feap.dt.schemas import PartyIn, ShipmentIn, ShipmentItemIn


def _model(**overrides) -> ShipmentIn:
    base = dict(
        seller=PartyIn(name="Alpha Trade Co., Ltd", country="CN"),
        buyer=PartyIn(name="ОсОО «Бета Импорт»", inn="01234567890123"),
        incoterms="CIF",
        contract_currency="USD",
        places_total=10,
        items=[
            ShipmentItemIn(description="Щётки зубные электрические", price=Decimal("500"),
                           net_kg=Decimal("100"), gross_kg=Decimal("110"), sku="TB-01"),
            ShipmentItemIn(description="Насадки для щёток", price=Decimal("500"),
                           net_kg=Decimal("50"), gross_kg=Decimal("60")),
        ],
    )
    base.update(overrides)
    return ShipmentIn(**base)


def _invoice_payload(**doc) -> dict:
    return {"doc_id": "d1", "kind": "invoice", "doc": doc}


class TestNumericFindings:
    def test_no_docs_no_findings(self):
        assert numeric_findings(_model(), []) == []

    def test_x01_invoice_total_diverges(self):
        docs = [_invoice_payload(number="INV-1", total_amount="1500")]
        rules = [f.rule for f in numeric_findings(_model(), docs)]
        assert "X-01" in rules

    def test_x01_within_tolerance_silent(self):
        docs = [_invoice_payload(total_amount="1002")]  # 0.2% от 1000
        assert numeric_findings(_model(), docs) == []

    def test_x02_currency_mismatch(self):
        docs = [_invoice_payload(currency="EUR", total_amount="1000")]
        findings = numeric_findings(_model(), docs)
        assert [f.rule for f in findings] == ["X-02"]
        assert findings[0].severity == "warning"

    def test_x06_incoterms_mismatch_case_insensitive(self):
        docs = [_invoice_payload(incoterms="fob", total_amount="1000")]
        assert [f.rule for f in numeric_findings(_model(), docs)] == ["X-06"]
        docs_ok = [_invoice_payload(incoterms="cif", total_amount="1000")]
        assert numeric_findings(_model(), docs_ok) == []

    def test_x03_x04_packing_weights(self):
        docs = [{
            "doc_id": "d2", "kind": "packing",
            "doc": {"net_total_kg": "200", "gross_total_kg": "171", "number": "PL-1"},
        }]
        # нетто досье 150 vs 200 → X-03; брутто 170 vs 171 (0.6% < 2%) → тихо
        rules = [f.rule for f in numeric_findings(_model(), docs)]
        assert rules == ["X-03"]

    def test_x03_skipped_when_weights_incomplete(self):
        model = _model()
        model.items[1].net_kg = None
        docs = [{"doc_id": "d2", "kind": "packing", "doc": {"net_total_kg": "999"}}]
        assert numeric_findings(model, docs) == []

    def test_x04_places(self):
        docs = [{"doc_id": "d2", "kind": "packing", "doc": {"packages_total": 8}}]
        findings = numeric_findings(_model(), docs)
        assert [f.rule for f in findings] == ["X-04"]

    def test_x05_cmr_gross(self):
        docs = [{"doc_id": "d3", "kind": "cmr", "doc": {"gross_kg": "300"}}]
        assert [f.rule for f in numeric_findings(_model(), docs)] == ["X-05"]

    def test_contract_currency_and_incoterms(self):
        docs = [{
            "doc_id": "d4", "kind": "contract",
            "doc": {"currency": "CNY", "incoterms": "EXW", "number": "К-1"},
        }]
        rules = sorted(f.rule for f in numeric_findings(_model(), docs))
        assert rules == ["X-02", "X-06"]


class TestPairBuilding:
    def test_party_pairs(self):
        docs = [_invoice_payload(
            seller={"name": "Alpha Trade", "country": "CN"},
            buyer={"name": "Beta Import LLC"},
        )]
        pairs = build_party_pairs(_model(), docs)
        assert [p["pair_id"] for p in pairs] == ["d1:seller", "d1:buyer"]
        assert "Alpha Trade Co., Ltd" in pairs[0]["shipment_value"]

    def test_cmr_maps_sender_receiver(self):
        docs = [{"doc_id": "d3", "kind": "cmr",
                 "doc": {"sender": {"name": "Alpha"}, "receiver": {"name": "Beta"}}}]
        pairs = build_party_pairs(_model(), docs)
        assert {p["pair_id"] for p in pairs} == {"d3:sender", "d3:receiver"}

    def test_line_inputs_only_invoices(self):
        docs = [
            _invoice_payload(lines=[{"line_no": 1, "description": "Brush", "sku": "TB-01"}]),
            {"doc_id": "d2", "kind": "packing",
             "doc": {"lines": [{"line_no": 1, "description": "x"}]}},
        ]
        lines = build_line_inputs(_model(), docs)
        assert len(lines) == 1
        assert lines[0]["description"] == "Brush"


class TestMatchEntitiesHarness:
    async def test_no_inputs_no_llm(self):
        out = await match_entities(_model(), [])
        assert out == {"parties": [], "invoice_lines": [], "dropped": [], "llm_used": False}

    async def test_harness_filters(self, monkeypatch):
        docs = [_invoice_payload(
            seller={"name": "Alpha Trade"},
            lines=[{"line_no": 1, "description": "Brush", "sku": "TB-01"},
                   {"line_no": 2, "description": "Heads"}],
        )]

        async def fake_llm(**kw):
            return EntityMatchResult(
                comparison_review="…",
                parties=[
                    PartyMatch(pair_id="d1:seller", verdict="match",
                               match_confidence=0.95, reasoning="транслитерация"),
                    PartyMatch(pair_id="d1:buyer", verdict="match",
                               match_confidence=0.9, reasoning="нет такой пары"),
                    PartyMatch(pair_id="d1:seller", verdict="mismatch",
                               match_confidence=0.3, reasoning="сомнительно"),
                ],
                invoice_lines=[
                    LineMatch(doc_line_no=1, shipment_position=1,
                              match_confidence=0.9, reasoning="sku"),
                    LineMatch(doc_line_no=2, shipment_position=99,
                              match_confidence=0.9, reasoning="битая позиция"),
                    LineMatch(doc_line_no=99, shipment_position=1,
                              match_confidence=0.9, reasoning="битая строка"),
                ],
            )

        monkeypatch.setattr(crosscheck, "llm_classify", fake_llm)
        out = await match_entities(_model(), docs)

        assert out["llm_used"] is True
        # d1:buyer не выдавался → отброшен; низкий confidence → uncertain
        verdicts = [(p["pair_id"], p["verdict"]) for p in out["parties"]]
        assert ("d1:seller", "match") in verdicts
        assert ("d1:seller", "uncertain") in verdicts
        assert all(pid != "d1:buyer" for pid, _ in verdicts)
        # строка 99 отброшена; позиция 99 → null
        lines = {ln["doc_line_no"]: ln for ln in out["invoice_lines"]}
        assert set(lines) == {1, 2}
        assert lines[1]["shipment_position"] == 1
        assert lines[2]["shipment_position"] is None
        # 4 нарушения: чужая пара, низкий confidence, битая позиция, битая строка
        assert len(out["dropped"]) == 4
