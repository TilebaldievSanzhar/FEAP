"""Фото/скриншот → карточка товара: подготовка изображений и нормализация ответа модели."""

from __future__ import annotations

import base64
from decimal import Decimal

import pymupdf
import pytest

from feap.calc import image_card
from feap.calc.image_card import (
    ImageInput,
    ImageInputError,
    build_output,
    extract_card,
    sniff_image,
    to_data_uri,
)
from feap.calc.schemas import ImagePrice, ImageProductCard, ImageSpec
from feap.llm import client as llm_client
from feap.llm.openrouter_provider import _with_images


def _png(width: int, height: int) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), True)
    pix.clear_with(180)
    return pix.tobytes("png")


def _card(**over) -> ImageProductCard:
    base = dict(
        is_product=True, product_identification="кастрюля",
        description_ru="Кастрюля литая из алюминия с антипригарным покрытием, для приготовления пищи.",
        material="алюминий", manufacturer="Zhejiang Supor Co., Ltd. (浙江苏泊尔股份有限公司)",
        origin_country="cn", origin_evidence="страница 1688, фабрика в Китае",
        price=ImagePrice(amount="12,50", currency="cny", per="шт", min_order_qty="100"),
        specs=[ImageSpec(name="Объём", value="3 л"), ImageSpec(name="Диаметр", value="24 см")],
    )
    base.update(over)
    return ImageProductCard(**base)


def test_sniff_by_signature_not_extension():
    assert sniff_image(_png(10, 10)) == "png"
    assert sniff_image(b"\xff\xd8\xff\xe0rest") == "jpeg"
    assert sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    with pytest.raises(ImageInputError):
        sniff_image(b"%PDF-1.7 ...")


def test_large_screenshot_is_downscaled_to_jpeg():
    uri = to_data_uri(_png(4200, 1800))
    head, b64 = uri.split(",", 1)
    assert head == "data:image/jpeg;base64"
    pix = pymupdf.Pixmap(base64.b64decode(b64))
    assert max(pix.width, pix.height) <= image_card.MAX_SIDE


def test_small_image_kept_as_is():
    data = _png(800, 600)
    assert to_data_uri(data) == "data:image/png;base64," + base64.b64encode(data).decode()


def test_too_big_file_rejected():
    with pytest.raises(ImageInputError):
        to_data_uri(b"\xff\xd8\xff" + b"0" * image_card.MAX_IMAGE_BYTES)


def test_prefill_is_normalized():
    out = build_output(_card(), images=2)
    p = out.prefill
    assert p.origin_country == "CN"
    assert p.currency == "CNY" and p.unit_price == Decimal("12.50")
    assert p.qty_unit == "шт" and p.min_order_qty == "100"
    assert out.images == 2
    assert out.classify_description.startswith("Кастрюля литая из алюминия")
    assert "Объём: 3 л" in out.classify_description


def test_price_without_currency_is_dropped():
    p = build_output(_card(price=ImagePrice(amount="12.5", currency="юань")), 1).prefill
    assert p.unit_price is None and p.currency is None


def test_unclear_origin_is_dropped():
    assert build_output(_card(origin_country="China"), 1).prefill.origin_country is None


def test_not_a_product_has_no_description():
    card = _card(is_product=False, not_product_reason="на изображении счёт")
    assert build_output(card, 1).classify_description == ""


async def test_extract_card_sends_images_to_level_97(monkeypatch):
    seen = {}

    async def fake_classify(**kwargs):
        seen.update(kwargs)
        return _card()

    monkeypatch.setattr(image_card, "llm_classify", fake_classify)
    out = await extract_card([ImageInput(_png(50, 50)), ImageInput(_png(60, 40))], note="для кухни")
    assert seen["level"] == 97
    assert len(seen["images"]) == 2 and seen["images"][0].startswith("data:image/png;base64,")
    assert "для кухни" in seen["user_message"]
    assert out.prefill.origin_country == "CN"


async def test_extract_card_limits():
    with pytest.raises(ImageInputError):
        await extract_card([])
    with pytest.raises(ImageInputError):
        await extract_card([ImageInput(_png(5, 5))] * (image_card.MAX_IMAGES + 1))


def test_multimodal_message_parts():
    parts = _with_images("текст", ["data:image/png;base64,AAA"])
    assert parts == [
        {"type": "text", "text": "текст"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]


async def test_facade_passes_images_only_when_given(monkeypatch):
    calls = []

    class FakeProvider:
        async def classify(self, **kwargs):
            calls.append(kwargs)
            return _card()

    monkeypatch.setattr(llm_client, "get_provider", lambda: FakeProvider())
    await llm_client.classify(level=0, system_prompt="s", user_message="u", output_model=ImageProductCard)
    await llm_client.classify(level=97, system_prompt="s", user_message="u",
                              output_model=ImageProductCard, images=["data:x"])
    assert "images" not in calls[0]
    assert calls[1]["images"] == ["data:x"]
