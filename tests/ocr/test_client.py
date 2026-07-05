"""OCR provider client — verify happy path + error mapping without real HTTP."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from feap.ocr.client import OcrClient
from feap.ocr.errors import OcrProviderError, OcrProviderTimeout

FIXTURES = Path(__file__).parent.parent / "fixtures" / "ocr"


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def _build_client_with_transport(transport: httpx.MockTransport) -> OcrClient:
    c = OcrClient(base_url="http://mock/ocr/process", timeout_s=10)
    # Swap the internal lazy-init client with one that uses our transport.
    c._client = httpx.AsyncClient(
        base_url="http://mock", transport=transport, timeout=10.0,
        follow_redirects=True,
    )
    return c


@pytest.mark.asyncio
async def test_extract_happy_path_invoice():
    fixture = json.loads((FIXTURES / "invoice_response.json").read_text(encoding="utf-8"))

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert b"filename=" in req.content or b"name=\"file\"" in req.content
        return httpx.Response(200, json=fixture)

    client = _build_client_with_transport(_mock_transport(handler))
    try:
        resp = await client.extract(
            filename="1_1.pdf", content=b"%PDF-stub", content_type="application/pdf",
        )
    finally:
        await client.aclose()

    assert resp.status == "completed"
    assert resp.data is not None
    assert "Global Trade Distribution" in resp.data.text


@pytest.mark.asyncio
async def test_extract_maps_5xx_to_provider_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    client = _build_client_with_transport(_mock_transport(handler))
    try:
        with pytest.raises(OcrProviderError) as excinfo:
            await client.extract(
                filename="x.pdf", content=b"x", content_type="application/pdf",
            )
    finally:
        await client.aclose()

    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_extract_maps_4xx_to_provider_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad file")

    client = _build_client_with_transport(_mock_transport(handler))
    try:
        with pytest.raises(OcrProviderError) as excinfo:
            await client.extract(
                filename="x.pdf", content=b"x", content_type="application/pdf",
            )
    finally:
        await client.aclose()

    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_extract_timeout_maps_to_timeout_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    client = _build_client_with_transport(_mock_transport(handler))
    try:
        with pytest.raises(OcrProviderTimeout):
            await client.extract(
                filename="x.pdf", content=b"x", content_type="application/pdf",
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_extract_non_json_body_maps_to_provider_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>oops</html>")

    client = _build_client_with_transport(_mock_transport(handler))
    try:
        with pytest.raises(OcrProviderError):
            await client.extract(
                filename="x.pdf", content=b"x", content_type="application/pdf",
            )
    finally:
        await client.aclose()
