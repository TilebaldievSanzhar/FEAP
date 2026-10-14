"""Фото или скриншот товара → карточка товара для расчёта (LLM уровень 97, vision).

Модель читает изображение и возвращает описание для каскада классификации и
подсказки формы расчёта (страна, производитель, цена за единицу). Цена — это
прочитанное с изображения значение, а не расчёт: пользователь её подтверждает,
платежи считает calc.engine.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from feap.calc.schemas import ImageCardOut, ImagePrefill, ImageProductCard
from feap.llm.client import classify as llm_classify
from feap.prompts.registry import render

logger = logging.getLogger(__name__)

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 8 * 1024 * 1024
#: Длинная сторона после уменьшения: скриншот 1688 читается, токенов меньше.
MAX_SIDE = 2000
_SPECS_IN_DESCRIPTION = 6


class ImageInputError(ValueError):
    """Файл не изображение, слишком большой или их слишком много."""


@dataclass(frozen=True)
class ImageInput:
    data: bytes
    filename: str | None = None


def sniff_image(data: bytes) -> str:
    """Формат по сигнатуре (а не по Content-Type от клиента): jpeg | png | webp."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise ImageInputError("Поддерживаются изображения JPEG, PNG и WebP")


def to_data_uri(data: bytes) -> str:
    """Проверить и при необходимости уменьшить изображение → data URI для модели."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageInputError(f"Изображение больше {MAX_IMAGE_BYTES // (1024 * 1024)} МБ")
    kind = sniff_image(data)
    try:
        import pymupdf

        pix = pymupdf.Pixmap(data)
        if max(pix.width, pix.height) > MAX_SIDE:
            while max(pix.width, pix.height) > MAX_SIDE:
                pix.shrink(1)
            if pix.alpha:
                pix = pymupdf.Pixmap(pix, 0)
            data, kind = pix.tobytes("jpeg", jpg_quality=85), "jpeg"
    except ImageInputError:
        raise
    except Exception:  # noqa: BLE001 — не смогли уменьшить (напр. WebP) → отдаём как есть
        logger.debug("image downscale skipped", exc_info=True)
    return f"data:image/{kind};base64,{base64.b64encode(data).decode('ascii')}"


def _iso2(value: str | None) -> str | None:
    v = (value or "").strip().upper()
    return v if re.fullmatch(r"[A-Z]{2}", v) else None


def _iso_currency(value: str | None) -> str | None:
    v = (value or "").strip().upper()
    return v if re.fullmatch(r"[A-Z]{3}", v) else None


def _amount(value: str | None) -> Decimal | None:
    raw = (value or "").replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None
    return amount if amount > 0 else None


def classify_description(card: ImageProductCard) -> str:
    """Текст для каскада: описание + ключевые характеристики (без цены и бренда)."""
    parts = [card.description_ru.strip()]
    specs = [f"{s.name}: {s.value}" for s in card.specs[:_SPECS_IN_DESCRIPTION]
             if s.name.strip() and s.value.strip()]
    if specs:
        parts.append("Характеристики: " + "; ".join(specs))
    return ". ".join(p.rstrip(".") for p in parts if p) + "."


def build_output(card: ImageProductCard, images: int) -> ImageCardOut:
    price = card.price
    unit_price = _amount(price.amount) if price else None
    currency = _iso_currency(price.currency) if price else None
    prefill = ImagePrefill(
        origin_country=_iso2(card.origin_country),
        manufacturer=(card.manufacturer or "").strip() or None,
        unit_price=unit_price if currency else None,
        currency=currency if unit_price else None,
        qty_unit=(price.per or "").strip() or None if price else None,
        min_order_qty=(price.min_order_qty or "").strip() or None if price else None,
    )
    return ImageCardOut(
        card=card,
        classify_description=classify_description(card) if card.is_product else "",
        prefill=prefill,
        images=images,
    )


async def extract_card(images: list[ImageInput], note: str | None = None) -> ImageCardOut:
    if not images:
        raise ImageInputError("Нужно хотя бы одно изображение")
    if len(images) > MAX_IMAGES:
        raise ImageInputError(f"Не больше {MAX_IMAGES} изображений одного товара")
    uris = [to_data_uri(img.data) for img in images]

    user_message = f"Изображений одного товара: {len(uris)}."
    if note and note.strip():
        user_message += f"\nКомментарий пользователя: {note.strip()[:1000]}"
    card = await llm_classify(
        level=97,
        system_prompt=render("system/level97_image_card.jinja2"),
        user_message=user_message,
        output_model=ImageProductCard,
        images=uris,
    )
    return build_output(card, len(uris))
