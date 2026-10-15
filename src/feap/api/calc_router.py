"""Расчёт по товару без досье и паспорт товара.

POST /api/v1/calc                         — платежи и себестоимость партии (без входа)
POST /api/v1/calc/passport.pdf            — PDF паспорта из расчёта (без входа)
POST /api/v1/calc/from-image              — фото/скриншот → карточка товара (LLM, vision)
GET  /api/v1/catalog/{id}/passport.pdf    — PDF паспорта записи «Товары» (организация)

В расчёте и паспорте LLM нет: справочные данные + сборщик проекта ДТ, поэтому
они открыты без входа, как справка по коду. Распознавание изображения — вызов
LLM: тот же доступ и учёт использования, что у классификации.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from feap.api.deps import get_db
from feap.auth.access import ensure_owner
from feap.auth.deps import AuthContext, get_auth_context
from feap.calc.engine import CodeNotFound, run_calc
from feap.calc.image_card import (
    MAX_IMAGE_BYTES,
    MAX_IMAGES,
    ImageInput,
    ImageInputError,
    extract_card,
)
from feap.calc.lookup import build_code_lookup
from feap.calc.passport_pdf import render_passport_pdf
from feap.calc.schemas import (
    CalcIn,
    CalcOut,
    CalcParams,
    ClassificationNote,
    ImageCardOut,
    PassportIn,
    PassportPdfIn,
)
from feap.db.models import ProductCatalogItem
from feap.db.queries import get_ancestors, get_leaf_by_code
from feap.dt.exporters.pdf import PdfFontError
from feap.usage import record_usage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["calc"])


@router.post("/calc", response_model=CalcOut)
async def calculate(payload: CalcIn, db: AsyncSession = Depends(get_db)) -> CalcOut:
    """Платежи (графа 47 + сбор) и себестоимость ввоза партии на дату.

    Тот же сборщик, что у проекта ДТ: суммы совпадают с проектом по такой же
    позиции. Без решения по мере защиты рынка — needs_input (как в досье).
    """
    try:
        return await run_calc(db, payload)
    except CodeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _passport_pdf(
    db: AsyncSession,
    *,
    hs_code: str,
    title: str,
    sku: str | None,
    params: CalcParams | None,
    classification: ClassificationNote | None,
    on_date: dt.date | None,
    description: str | None,
) -> bytes:
    node = await get_leaf_by_code(db, hs_code)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Код {hs_code} не найден в номенклатуре")
    day = on_date or dt.date.today()
    origin = params.origin_country if params else None
    lookup = await build_code_lookup(db, hs_code, origin=origin, on_date=day)
    assert lookup is not None  # лист уже найден выше
    chain = [(n.level, n.code_formatted or n.code, n.name) for n in await get_ancestors(db, node)]

    calc_out: CalcOut | None = None
    if params is not None and (params.price or (params.unit_price and params.qty)):
        calc_in = CalcIn(
            **params.model_dump(), hs_code=hs_code, description=description, rate_date=day,
        )
        calc_out = await run_calc(db, calc_in)
    try:
        return render_passport_pdf(
            title=title, sku=sku, lookup=lookup, tree_chain=chain,
            classification=classification, params=params, calc=calc_out, on_date=day,
            description=description,
        )
    except PdfFontError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc


def _pdf_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/calc/passport.pdf")
async def calc_passport_pdf(
    payload: PassportPdfIn, db: AsyncSession = Depends(get_db),
) -> Response:
    """Паспорт товара прямо из расчёта /calc — без сохранения в «Товары»."""
    calc = payload.calc
    params = CalcParams(**calc.model_dump(exclude={"hs_code", "description", "rate_date"}))
    content = await _passport_pdf(
        db, hs_code=calc.hs_code,
        title=payload.title or calc.description or "Товар",
        sku=payload.sku, params=params, classification=payload.classification,
        on_date=calc.rate_date, description=calc.description,
    )
    return _pdf_response(content, f"passport_{calc.hs_code}.pdf")


@router.get("/catalog/{item_id}/passport.pdf")
async def catalog_passport_pdf(
    item_id: uuid.UUID,
    on_date: dt.date | None = Query(None, description="Дата ставок и курсов; по умолчанию сегодня"),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> Response:
    """Паспорт записи «Товары»: сохранённые параметры партии, ставки на дату."""
    row = await db.get(ProductCatalogItem, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Товар не найден")
    ensure_owner(row.org_id, ctx.org_id, "Товар не найден")
    if not row.hs_code:
        raise HTTPException(status_code=422, detail="У товара нет кода ТН ВЭД — паспорт не собрать")
    passport = PassportIn.model_validate(row.passport or {})
    content = await _passport_pdf(
        db, hs_code=row.hs_code, title=row.description, sku=row.sku,
        params=passport.calc, classification=passport.classification,
        on_date=on_date, description=row.description,
    )
    return _pdf_response(content, f"passport_{row.sku}.pdf")


@router.post("/calc/from-image", response_model=ImageCardOut)
async def calc_from_image(
    files: list[UploadFile] = File(..., description=f"1–{MAX_IMAGES} изображения одного товара"),
    note: str | None = Form(None, max_length=1000, description="Что важно знать о товаре"),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> ImageCardOut:
    """Фото товара или скриншот карточки с маркетплейса → описание для классификации
    и подсказки формы расчёта (страна, производитель, цена за единицу — проверить)."""
    if len(files) > MAX_IMAGES:
        raise HTTPException(422, f"Не больше {MAX_IMAGES} изображений одного товара")
    images = [
        ImageInput(data=await f.read(MAX_IMAGE_BYTES + 1), filename=f.filename) for f in files
    ]
    try:
        out = await extract_card(images, note)
    except ImageInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — сбой модели/провайдера, не данных
        logger.exception("image card extraction failed")
        raise HTTPException(
            status_code=502, detail="Не удалось распознать изображение — попробуйте ещё раз",
        ) from exc
    record_usage(
        db, org_id=ctx.org_id, kind="image_card", quantity=1, ref_type="calc",
        meta={"images": out.images, "is_product": out.card.is_product},
    )
    await db.commit()
    return out
