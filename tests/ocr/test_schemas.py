"""Schema sanity checks — round-trip provider fixtures through our pydantic models."""
from __future__ import annotations

import json
from pathlib import Path

from feap.ocr.schemas import (
    OcrFileExtraction,
    OcrJobResponse,
    OcrProviderResponse,
    StructuredProduct,
    StructuredProducts,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "ocr"


def test_invoice_fixture_parses():
    raw = json.loads((FIXTURES / "invoice_response.json").read_text(encoding="utf-8"))
    resp = OcrProviderResponse.model_validate(raw)
    assert resp.status == "completed"
    assert resp.data is not None
    assert resp.data.text.startswith("## Global Trade Distribution FZE")
    assert resp.data.processing_time is not None


def test_tech_description_fixture_parses():
    raw = json.loads((FIXTURES / "tech_description_response.json").read_text(encoding="utf-8"))
    resp = OcrProviderResponse.model_validate(raw)
    assert resp.status == "completed"
    assert resp.data is not None
    # Cyrillic must round-trip intact
    assert "Графическая карта" in resp.data.text


def test_structured_products_accepts_minimal_product():
    sp = StructuredProducts(
        products=[
            StructuredProduct.model_validate({
                "product_card": {
                    "original_description": "X",
                    "normalized_description": "X",
                },
                "source_ref": {"file_index": 0},
            })
        ],
        skipped_lines=[],
    )
    assert len(sp.products) == 1
    assert sp.products[0].source_ref["file_index"] == 0


def test_ocr_file_extraction_round_trip():
    ex = OcrFileExtraction(
        file_index=2, filename="a.pdf", text="hello", processing_time_s=1.5,
    )
    dumped = ex.model_dump()
    restored = OcrFileExtraction.model_validate(dumped)
    assert restored.file_index == 2
    assert restored.filename == "a.pdf"
    assert restored.processing_time_s == 1.5
