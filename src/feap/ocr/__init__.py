"""OCR integration — upload documents, extract raw text, structure into ProductCard[].

Flow (see docs/ocr_integration.md):
    upload → OcrJob[pending]
    arq task run_ocr_job:
        stage=extracting  — POST each file to OCR provider, store raw text
        stage=structuring — single LLM call converts concat-text → ProductCard[]
        stage=completed
    (optional) auto_classify=True → chain into classification_jobs
"""
from feap.ocr.errors import (
    OcrError,
    OcrFileRejected,
    OcrProviderError,
    OcrProviderTimeout,
    OcrStructureError,
)

__all__ = [
    "OcrError",
    "OcrFileRejected",
    "OcrProviderError",
    "OcrProviderTimeout",
    "OcrStructureError",
]
