"""HTTP client for the internal OCR provider (ocr.trade.kg /ocr/process).

The provider is sync: one POST, multipart form with `file`, returns
a JSON envelope containing extracted text. We keep a single
AsyncClient per process to reuse the connection pool.

The call blocks for roughly processing_time seconds (observed
~3–15 s per single-page PDF). Callers MUST run this inside an
arq worker task, never inline in a FastAPI request handler.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from feap.config import settings
from feap.ocr.errors import OcrProviderError, OcrProviderTimeout
from feap.ocr.schemas import OcrProviderResponse

logger = logging.getLogger(__name__)


class OcrClient:
    """Thin async wrapper around POST /ocr/process."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self._url = base_url or settings.OCR_PROVIDER_URL
        self._timeout = timeout_s or settings.OCR_PROVIDER_TIMEOUT_S
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
            )
        return self._client

    async def extract(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> OcrProviderResponse:
        """Upload one file, return the parsed provider envelope.

        Raises OcrProviderTimeout / OcrProviderError on non-2xx or transport
        errors. Callers are expected to treat an empty `data.text` as a soft
        warning (file unreadable), not a hard failure.
        """
        client = await self._get_client()
        files = {"file": (filename, content, content_type)}
        try:
            resp = await client.post(self._url, files=files)
        except httpx.TimeoutException as exc:
            raise OcrProviderTimeout(
                f"OCR provider timed out after {self._timeout}s for '{filename}'"
            ) from exc
        except httpx.HTTPError as exc:
            raise OcrProviderError(f"OCR provider transport error: {exc}") from exc

        if resp.status_code >= 500:
            raise OcrProviderError(
                f"OCR provider 5xx: {resp.status_code} {resp.text[:300]}",
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise OcrProviderError(
                f"OCR provider 4xx: {resp.status_code} {resp.text[:300]}",
                status_code=resp.status_code,
            )

        try:
            payload: dict[str, Any] = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise OcrProviderError(
                f"OCR provider returned non-JSON body: {resp.text[:300]}"
            ) from exc

        return OcrProviderResponse.model_validate(payload)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


_singleton: OcrClient | None = None


def get_ocr_client() -> OcrClient:
    """Process-wide singleton. Reuses httpx connection pool."""
    global _singleton
    if _singleton is None:
        _singleton = OcrClient()
    return _singleton
