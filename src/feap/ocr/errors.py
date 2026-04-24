"""OCR module error hierarchy."""
from __future__ import annotations


class OcrError(Exception):
    """Base class for OCR-related failures."""


class OcrFileRejected(OcrError):
    """File rejected at upload time (size, MIME, count limits)."""


class OcrProviderError(OcrError):
    """Upstream OCR provider returned an error response."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OcrProviderTimeout(OcrProviderError):
    """Upstream OCR provider did not respond within the timeout."""


class OcrStructureError(OcrError):
    """LLM failed to structure raw text into ProductCard[]."""
