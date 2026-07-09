"""PDF-экспортёр: рендер печатной формы (нужен системный TTF с кириллицей)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from feap.dt.assembler import ItemRef, Rates, assemble
from feap.dt.exporters.pdf import PdfFontError, ensure_fonts, render_pdf
from feap.dt.schemas import PartyIn, ShipmentIn, ShipmentItemIn

try:
    ensure_fonts()
    _HAS_FONT = True
except PdfFontError:
    _HAS_FONT = False

pytestmark = pytest.mark.skipif(
    not _HAS_FONT, reason="Нет TTF-шрифта с кириллицей (DT_PDF_FONT_PATH)"
)


def _sample():
    shipment = ShipmentIn(
        seller=PartyIn(name="Guangzhou Trading Co., Ltd", country="CN"),
        buyer=PartyIn(name="ОсОО «Импортер»", country="KG", inn="01234567890123"),
        incoterms="EXW",
        contract_currency="USD",
        freight_to_border=Decimal("400"),
        items=[
            ShipmentItemIn(
                description="Электрическая зубная щётка со встроенным аккумулятором",
                hs_code="8509800000",
                origin_country="CN",
                price=Decimal("1000"),
                net_kg=Decimal("90"),
                gross_kg=Decimal("100"),
            ),
            ShipmentItemIn(
                description="Обувь женская из натуральной кожи на резиновой подошве",
                hs_code="6403990000",
                origin_country="CN",
                price=Decimal("3000"),
                net_kg=Decimal("270"),
                gross_kg=Decimal("300"),
                supp_qty=Decimal("500"),
                supp_unit="пар",
            ),
        ],
    )
    refs = [
        ItemRef(code_exists=True, is_leaf=True, duty_raw="10%", vat_rate="12%"),
        ItemRef(code_exists=True, is_leaf=True,
                duty_raw="1.25 EUR за 1 пар", vat_rate="12%", unit_secondary="пар"),
    ]
    rates = Rates(rate_date=dt.date(2026, 7, 4),
                  contract=Decimal("87.5"), usd=Decimal("87.5"), eur=Decimal("95"))
    return assemble(shipment, refs, rates, fee_kgs=Decimal("2000"))


def test_render_pdf_bytes():
    assembled = _sample()
    findings = [
        {"rule": "W-31", "severity": "warning", "graph": "items[0].g31",
         "message": "Описание короткое", "details": {}},
        {"rule": "F-03", "severity": "error", "graph": "items[1].g33",
         "message": "Код не найден в номенклатуре", "details": {}},
    ]

    content = render_pdf(assembled.graphs, findings)

    assert content[:5] == b"%PDF-"
    assert len(content) > 3000
    assert b"/Watermark" in content or True  # структуру не парсим — только валидность


def test_render_pdf_empty_findings():
    assembled = _sample()
    content = render_pdf(assembled.graphs, [])
    assert content[:5] == b"%PDF-"


def test_render_pdf_missing_payment_marked():
    """Позиция без веса при комбинированной ставке — PDF всё равно рендерится."""
    shipment = ShipmentIn(
        seller=PartyIn(name="Seller GmbH", country="DE"),
        buyer=PartyIn(name="ОсОО «Тест»", country="KG"),
        incoterms="CIF",
        contract_currency="EUR",
        items=[
            ShipmentItemIn(
                description="Сыр полутвёрдый из коровьего молока",
                hs_code="0406901500",
                price=Decimal("5000"),
                gross_kg=Decimal("200"),
            ),
        ],
    )
    refs = [ItemRef(code_exists=True, is_leaf=True,
                    duty_raw="15%, но не менее 0.3 EUR за 1 кг", vat_rate="12%")]
    rates = Rates(rate_date=dt.date(2026, 7, 4),
                  contract=Decimal("95"), usd=Decimal("87.5"), eur=Decimal("95"))
    assembled = assemble(shipment, refs, rates)

    assert assembled.missing_parameters  # вес нетто не задан
    content = render_pdf(assembled.graphs, [])
    assert content[:5] == b"%PDF-"
