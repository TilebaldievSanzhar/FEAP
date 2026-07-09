"""XLSX-экспортёр: структура книги и ключевые ячейки."""

from __future__ import annotations

import datetime as dt
import io
from decimal import Decimal

from openpyxl import load_workbook

from feap.dt.assembler import ItemRef, Rates, assemble
from feap.dt.schemas import PartyIn, ShipmentIn, ShipmentItemIn
from feap.dt.exporters.xlsx import render_xlsx


def _sample():
    shipment = ShipmentIn(
        seller=PartyIn(name="Seller GmbH", country="DE"),
        buyer=PartyIn(name="ОсОО «Тест»", country="KG", inn="01234567890123"),
        incoterms="CIF",
        contract_currency="EUR",
        items=[
            ShipmentItemIn(
                description="Станок токарный металлорежущий с ЧПУ, новый, 4 кВт",
                hs_code="8458112000",
                origin_country="DE",
                price=Decimal("25000"),
                net_kg=Decimal("1800"),
                gross_kg=Decimal("2000"),
            ),
        ],
    )
    refs = [ItemRef(code_exists=True, is_leaf=True, duty_raw="0%", vat_rate="12%")]
    rates = Rates(rate_date=dt.date(2026, 7, 4),
                  contract=Decimal("95"), usd=Decimal("87.5"), eur=Decimal("95"))
    return assemble(shipment, refs, rates)


def test_render_and_reload():
    assembled = _sample()
    findings = [
        {"rule": "W-31", "severity": "warning", "graph": "items[0].g31",
         "message": "Описание короткое", "details": {}},
    ]
    content = render_xlsx(assembled.graphs, findings)
    wb = load_workbook(io.BytesIO(content))

    assert wb.sheetnames == ["ДТ (шапка)", "Товары", "Платежи (47)", "Проверки"]

    ws = wb["ДТ (шапка)"]
    assert "ПРОЕКТ" in str(ws.cell(row=1, column=1).value)

    ws2 = wb["Товары"]
    assert ws2.cell(row=3, column=2).value == "Код ТН ВЭД (33)"
    assert ws2.cell(row=4, column=2).value == "8458112000"
    # Таможенная стоимость: 25 000 × 95 = 2 375 000
    assert ws2.cell(row=4, column=9).value == 2375000.0

    ws4 = wb["Проверки"]
    assert ws4.cell(row=4, column=1).value == "W-31"


def test_render_empty_findings():
    assembled = _sample()
    content = render_xlsx(assembled.graphs, [])
    wb = load_workbook(io.BytesIO(content))
    assert "Замечаний нет" in str(wb["Проверки"].cell(row=4, column=1).value)
