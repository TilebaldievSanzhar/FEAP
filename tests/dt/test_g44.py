"""Графа 44: маппинг видов документов, DocEntry, сборка в графы."""

from __future__ import annotations

import datetime as dt
import importlib.util
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from feap.dt.assembler import DocEntry, ItemRef, Rates, assemble
from feap.dt.documents import DOC_KIND_CODES, build_g44_entries
from feap.dt.schemas import PartyIn, ShipmentIn, ShipmentItemIn

RATES = Rates(rate_date=dt.date(2026, 7, 4), contract=Decimal("87.5"),
              usd=Decimal("87.5"), eur=Decimal("95"))


def _doc_row(kind: str, number: str | None = None,
             doc_date: dt.date | None = None, extracted: dict | None = None):
    return SimpleNamespace(
        id=uuid.uuid4(), kind=kind, number=number,
        doc_date=doc_date, extracted=extracted,
    )


class TestBuildG44Entries:
    def test_known_kinds_get_codes(self):
        rows = [
            _doc_row("invoice", "INV-1", dt.date(2026, 7, 1)),
            _doc_row("contract", "К-5"),
            _doc_row("cmr", "123456"),
        ]
        entries = build_g44_entries(rows)
        assert [e.code for e in entries] == ["04021", "03011", "02015"]
        assert entries[0].number == "INV-1"
        assert entries[0].date == "2026-07-01"

    def test_unknown_kind_without_code(self):
        entries = build_g44_entries([_doc_row("packing", "PL-1")])
        assert entries[0].code is None
        assert entries[0].kind == "packing"

    def test_permit_status_from_extracted(self):
        rows = [_doc_row(
            "permit", "KG-417/005",
            extracted={"permit_check": {"status": "not_found"}},
        )]
        assert build_g44_entries(rows)[0].permit_status == "not_found"

    def test_no_check_no_status(self):
        assert build_g44_entries([_doc_row("permit", "X-1")])[0].permit_status is None


def _model(**overrides) -> ShipmentIn:
    base = dict(
        seller=PartyIn(name="Alpha"), buyer=PartyIn(name="Beta"),
        incoterms="CIF", contract_currency="USD",
        deal_nature="010",
        items=[ShipmentItemIn(description="Товар для сборки граф", price=Decimal("100"),
                              preference="ПППП")],
    )
    base.update(overrides)
    return ShipmentIn(**base)


class TestAssembleNewGraphs:
    def test_g24_g36_g44_present(self):
        docs = [
            DocEntry(kind="invoice", code="04021", number="INV-1", date="2026-07-01"),
            DocEntry(kind="permit", code=None, number="P-9",
                     permit_status="found"),
        ]
        result = assemble(_model(), [ItemRef(vat_rate="12%")], RATES, documents=docs)
        header, item = result.graphs["header"], result.graphs["items"][0]
        assert header["g24"] == "010"
        assert item["g36"] == "ПППП"
        assert item["g44"] == [
            {"code": "04021", "kind": "invoice", "number": "INV-1", "date": "2026-07-01"},
            {"code": None, "kind": "permit", "number": "P-9", "date": None,
             "permit_status": "found"},
        ]

    def test_no_documents_empty_g44(self):
        result = assemble(_model(), [ItemRef(vat_rate="12%")], RATES)
        assert result.graphs["items"][0]["g44"] == []
        # дефолты новых полей не ломают старые досье
        old = _model(deal_nature=None)
        old.items[0].preference = None
        result2 = assemble(old, [ItemRef(vat_rate="12%")], RATES)
        assert result2.graphs["header"]["g24"] is None
        assert result2.graphs["items"][0]["g36"] is None


def test_doc_kind_codes_consistent_with_seed():
    """Каждый код из DOC_KIND_CODES обязан быть в сиде классификаторов —
    иначе F-09 будет помечать каждую графу 44 после наполнения БД."""
    seed_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "seed" / "classifiers.py"
    )
    spec = importlib.util.spec_from_file_location("seed_classifiers", seed_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    seeded_doc_kinds = {
        row["item_code"] for row in module.CLASSIFIERS
        if row["classifier_code"] == "doc_kinds"
    }
    assert set(DOC_KIND_CODES.values()) <= seeded_doc_kinds

    # и сам сид не содержит дублей (classifier, item)
    pairs = [(r["classifier_code"], r["item_code"]) for r in module.CLASSIFIERS]
    assert len(pairs) == len(set(pairs))

    # коды видов платежей графы 47 (вкл. 2050 антидемпинг) — тоже в сиде,
    # иначе F-09 пометит каждую строку пошлин защиты рынка
    from feap.dt.schemas import PAYMENT_CODES

    seeded_payments = {
        row["item_code"] for row in module.CLASSIFIERS
        if row["classifier_code"] == "payment_kinds"
    }
    assert set(PAYMENT_CODES.values()) <= seeded_payments
