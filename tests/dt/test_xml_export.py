"""XML-экспортёр R.055-draft: структура, well-formed, XSD-хук."""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from decimal import Decimal

from feap.config import settings
from feap.dt.assembler import DocEntry, ItemRef, Rates, assemble
from feap.dt.exporters.xml_r055 import (
    XMLNS,
    render_xml,
    validate_against_xsd,
)
from feap.dt.schemas import PartyIn, ShipmentIn, ShipmentItemIn

RATES = Rates(rate_date=dt.date(2026, 7, 4), contract=Decimal("87.5"),
              usd=Decimal("87.5"), eur=Decimal("95"))


def _graphs() -> dict:
    model = ShipmentIn(
        seller=PartyIn(name="Alpha Trading", country="CN"),
        buyer=PartyIn(name="ОсОО «Бета»", inn="01234567890123"),
        incoterms="CIF", contract_currency="USD", deal_nature="010",
        items=[
            ShipmentItemIn(description="Щётка зубная электрическая",
                           hs_code="8509800000", price=Decimal("1000"),
                           net_kg=Decimal("90"), gross_kg=Decimal("100")),
            ShipmentItemIn(description="Насадки", price=Decimal("500")),
        ],
    )
    refs = [ItemRef(code_exists=True, is_leaf=True, duty_raw="10%", vat_rate="12%"),
            ItemRef(vat_rate="12%")]
    docs = [DocEntry(kind="invoice", code="04021", number="INV-1", date="2026-07-01")]
    return assemble(model, refs, RATES, documents=docs,
                    shipment_ref="ship-1").graphs


def _ns(tag: str) -> str:
    return f"{{{XMLNS}}}{tag}"


class TestRenderXml:
    def setup_method(self):
        self.xml = render_xml(_graphs())
        self.root = ET.fromstring(self.xml)  # well-formed или исключение

    def test_declaration_and_root(self):
        assert self.xml.startswith(b"<?xml version='1.0' encoding='utf-8'?>")
        assert self.root.tag == _ns("DTDraft")
        assert self.root.get("structureVersion") == settings.DT_XML_STRUCTURE_VERSION
        assert self.root.get("procedure") == "IM40"

    def test_disclaimer_present(self):
        disclaimer = self.root.find(_ns("Disclaimer"))
        assert disclaimer is not None and "Проект" in disclaimer.text

    def test_header_graphs(self):
        header = self.root.find(_ns("Header"))
        assert header.find(_ns("G05")).text == "2"
        g22 = header.find(_ns("G22"))
        assert g22.get("currency") == "USD"
        assert g22.get("amount") == "1500.00"
        assert header.find(_ns("G24")).text == "010"
        seller = header.find(_ns("G02"))
        assert seller.find(_ns("Name")).text == "Alpha Trading"

    def test_items(self):
        goods = self.root.find(_ns("Goods"))
        assert goods.get("count") == "2"
        items = goods.findall(_ns("Item"))
        assert [i.get("number") for i in items] == ["1", "2"]
        assert items[0].find(_ns("G33")).text == "8509800000"
        # позиция без кода — элемент G33 опущен, а не пустой
        assert items[1].find(_ns("G33")) is None

    def test_g44_and_g47(self):
        item = self.root.find(_ns("Goods")).find(_ns("Item"))
        doc = item.find(_ns("G44")).find(_ns("Doc"))
        assert doc.get("code") == "04021"
        assert doc.get("number") == "INV-1"
        payments = item.find(_ns("G47")).findall(_ns("Payment"))
        kinds = {p.get("kind") for p in payments}
        assert {"duty", "vat"} <= kinds
        duty = next(p for p in payments if p.get("kind") == "duty")
        assert duty.get("amount") == "8750.00"

    def test_totals(self):
        totals = self.root.find(_ns("Totals"))
        assert totals.get("customsValue") == "131250.00"  # (1000+500)×87.5
        kinds = {t.get("kind") for t in totals.find(_ns("Payments"))}
        assert "duty" in kinds and "vat" in kinds

    def test_structure_version_override(self):
        xml = render_xml(_graphs(), structure_version="R.055/9.99")
        assert ET.fromstring(xml).get("structureVersion") == "R.055/9.99"


class TestXsdHook:
    def test_no_xsd_configured_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "DT_XML_XSD_PATH", "")
        assert validate_against_xsd(b"<a/>") is None

    def test_bad_xsd_path_reports_error(self, monkeypatch):
        monkeypatch.setattr(settings, "DT_XML_XSD_PATH", "C:/nonexistent/schema.xsd")
        errors = validate_against_xsd(render_xml(_graphs()))
        # либо xmlschema не установлен, либо XSD не загрузился — в обоих
        # случаях список непуст и объясняет причину
        assert errors and isinstance(errors, list)
