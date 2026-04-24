"""LLM step that turns raw OCR text into ProductCard[].

Single LLM call (level 98, medium tier). Input is concatenated
markdown-style text from N files. Output is a list of atomic
products ready for the classification cascade plus notes about
lines that were intentionally discarded.

Why one call for all files: the LLM can cross-reference — e.g.
invoice lists short descriptions, attached spec sheet fills in
material/power. Splitting per-file would lose that context.
"""
from __future__ import annotations

import logging

from feap.llm.client import classify as llm_classify
from feap.ocr.errors import OcrStructureError
from feap.ocr.schemas import OcrFileExtraction, StructuredProducts
from feap.prompts.registry import render

logger = logging.getLogger(__name__)


async def structure_products(
    extractions: list[OcrFileExtraction],
) -> StructuredProducts:
    """Run the LLM-structure step across all extracted texts.

    Caller is responsible for upstream orchestration (marking the
    OcrJob stage, persistence). This function only does the LLM call.
    """
    if not extractions:
        return StructuredProducts(products=[], skipped_lines=["no extractions provided"])

    # Keep only files that produced some text — totally empty ones tell
    # the LLM nothing and just burn tokens.
    usable = [e for e in extractions if e.text.strip()]
    if not usable:
        return StructuredProducts(
            products=[],
            skipped_lines=[f"file {e.filename}: empty OCR text" for e in extractions],
        )

    system_prompt = render("system/level98_ocr_structure.jinja2")
    user_message = render(
        "context/ocr_extraction.jinja2",
        extractions=[
            {
                "file_index": e.file_index,
                "filename": e.filename,
                "text": e.text,
            }
            for e in usable
        ],
    )

    try:
        result = await llm_classify(
            level=98,
            system_prompt=system_prompt,
            user_message=user_message,
            output_model=StructuredProducts,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("OCR structure LLM call failed")
        raise OcrStructureError(f"LLM structurer failed: {exc}") from exc

    logger.info(
        "OCR structurer returned %d products, %d skipped lines",
        len(result.products), len(result.skipped_lines),
    )
    return result
