"""PDF паспорта товара: рендер с расчётом и без него (нужен TTF с кириллицей)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from feap.calc.passport_pdf import render_passport_pdf
from feap.calc.schemas import CalcParams, ClassificationNote, ClassificationStep, CostLine
from feap.dt.exporters.pdf import PdfFontError, ensure_fonts
from feap.dt.schemas import RemedyDecision
from feap.schemas.api import (
    CodeLookupResponse,
    ExciseInfo,
    TradeRemedyInfo,
    TradeRemedyRateInfo,
    VatInfo,
)

from tests.calc.test_engine import calc_in, run

try:
    ensure_fonts()
    _HAS_FONT = True
except PdfFontError:
    _HAS_FONT = False

pytestmark = pytest.mark.skipif(not _HAS_FONT, reason="Нет TTF-шрифта с кириллицей")

LOOKUP = CodeLookupResponse(
    code="7615101000", code_formatted="7615 10 100 0", name="литые", duty_rate="9%",
    vat=VatInfo(rate="12%"), excise=ExciseInfo(applicable=False),
    trade_remedies=[TradeRemedyInfo(
        measure_no="AD-32", kind="antidumping", title="Алюминиевая посуда", countries=["CN"],
        rates=[TradeRemedyRateInfo(rate_pct="21.89")], valid_to="2027-05-28",
    )],
    customs_fee="0.4% от таможенной стоимости ДТ, не менее 500 сом и не более 250000 сом — ПП КР № 79",
)
CHAIN = [
    (0, "XV", "Недрагоценные металлы и изделия из них"),
    (1, "76", "Алюминий и изделия из него"),
    (2, "7615", "Изделия столовые, кухонные или прочие изделия для бытовых нужд"),
    (4, "7615 10 100 0", "литые"),
]


def _text(content: bytes) -> str:
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def test_passport_with_calculation():
    calc = calc_in(
        remedy_decisions={"AD-32": RemedyDecision(decision="applies")},
        costs=[CostLine(kind="broker", amount=Decimal("15000"))],
    )
    out = run(calc)
    params = CalcParams(**calc.model_dump(exclude={"hs_code", "description", "rate_date"}))
    content = render_passport_pdf(
        title="Кастрюли литые алюминиевые", sku="POT-1", lookup=LOOKUP, tree_chain=CHAIN,
        classification=ClassificationNote(
            source="cascade", confidence=0.94,
            steps=[ClassificationStep(level="position", code="7615", name="Изделия кухонные",
                                      reasoning="ОПИ 1: текст позиции описывает посуду")],
        ),
        params=params, calc=out, on_date=dt.date(2026, 10, 14),
    )
    assert content[:5] == b"%PDF-"
    text = _text(content)
    for needle in ("Паспорт товара", "7615 10 100 0", "AD-32", "Себестоимость ввоза",
                   "388 015.65", "уверенность 0,94", "1 277.52"):
        assert needle in text, needle


def test_passport_rates_only_without_calculation():
    content = render_passport_pdf(
        title="Кастрюли", sku=None, lookup=LOOKUP, tree_chain=CHAIN, classification=None,
        params=None, calc=None, on_date=dt.date(2026, 10, 14),
    )
    text = _text(content)
    assert "Ставки и меры" in text and "может применяться" in text
    assert "Себестоимость ввоза" not in text
