"""API ДТ-ядра: досье поставок, проекты ДТ, каталог товаров, SGR-слой.

GET    /api/v1/shipments                 — список досье организации
POST   /api/v1/shipments                 — создать досье (+позиции)
GET    /api/v1/shipments/{id}            — досье с позициями
PUT    /api/v1/shipments/{id}            — обновить (позиции заменяются целиком)
DELETE /api/v1/shipments/{id}
POST   /api/v1/shipments/{id}/clone      — копия досье (без драфтов)
POST   /api/v1/shipments/{id}/classify   — batch-классификация позиций без кода
POST   /api/v1/shipments/{id}/dt-draft   — собрать проект ДТ (новая версия)
GET    /api/v1/dt-drafts/{id}            — графы + findings
GET    /api/v1/dt-drafts/{id}/export.xlsx
GET    /api/v1/dt-drafts/{id}/export.pdf
GET    /api/v1/catalog                   — каталог товаров (поиск q=)
GET    /api/v1/catalog/suggest           — подсказки с утверждённым кодом
POST   /api/v1/catalog                   — upsert по (org, sku)
DELETE /api/v1/catalog/{id}
POST   /api/v1/catalog/import-from-shipment/{id} — утвердить коды из досье

SGR-слой (Фаза 2, LLM по краям — деньги по-прежнему считает только код):
GET    /api/v1/shipments/{id}/documents             — документы досье
POST   /api/v1/shipments/{id}/documents             — привязать (text | ocr_job)
DELETE /api/v1/shipments/{id}/documents/{doc_id}
POST   /api/v1/shipments/{id}/documents/{doc_id}/extract — типизация + извлечение
POST   /api/v1/shipments/{id}/crosscheck            — X-xx (код) + сущности (LLM)
POST   /api/v1/shipments/{id}/items/{position}/describe31 — текст графы 31
POST   /api/v1/shipments/{id}/advise                — риск-советник
POST   /api/v1/dt-drafts/{id}/fix-suggestions       — предложения правок по findings

Авторизация (Track B1): организация и роль — из AuthContext (Bearer-токен;
в AUTH_MODE=optional без токена — прежний dev-режим X-Org-Id/'default').
Роли: чтение и экспорт — любая роль; запись и LLM-вызовы — declarant и выше
(LLM-эндпоинты тратят токены — viewer их не запускает).
Объекты чужой организации неотличимы от несуществующих (404).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from feap.api.deps import get_db, get_job_backend
from feap.auth.deps import AuthContext, get_auth_context, require_role
from feap.db.models import ProductCatalogItem
from feap.dt import (
    advisor,
    catalog,
    classify_link,
    crosscheck,
    describe31,
    documents,
    fixer,
    service,
    tulpar,
)
from feap.dt.documents import DocumentSourceError
from feap.dt.errors import NoItemsToClassify
from feap.dt.exporters.pdf import PdfFontError, render_pdf
from feap.dt.exporters.xlsx import render_xlsx
from feap.dt.exporters.xml_r055 import render_xml, validate_against_xsd
from feap.dt.extractors.schemas import EXTRACTABLE_KINDS
from feap.dt.schemas import (
    AdviseOut,
    CatalogImportOut,
    CatalogItemIn,
    CatalogItemOut,
    CatalogListOut,
    CrosscheckOut,
    DocumentIn,
    DocumentListOut,
    DocumentOut,
    DtDraftOut,
    Finding,
    FixOut,
    G31Out,
    PermitCheckOut,
    ShipmentClassifyOut,
    ShipmentIn,
    ShipmentListOut,
    ShipmentOut,
)
from feap.jobs.enqueue import JobBackend

router = APIRouter(prefix="/api/v1", tags=["dt"])


def _check_org(obj_org_id: uuid.UUID | None, ctx: AuthContext) -> None:
    """Объект другой организации неотличим от несуществующего."""
    if obj_org_id is not None and obj_org_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Не найдено")


# ── Досье ────────────────────────────────────────────────────────────────────

@router.get("/shipments", response_model=ShipmentListOut)
async def list_shipments(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> ShipmentListOut:
    entries, total = await service.list_shipments(
        session, ctx.org_id, status=status, limit=limit, offset=offset
    )
    return ShipmentListOut(items=entries, total=total)


@router.post("/shipments", response_model=ShipmentOut, status_code=201)
async def create_shipment(
    payload: ShipmentIn,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> ShipmentOut:
    require_role(ctx, "declarant")
    shipment = await service.create_shipment(session, ctx.org, payload)
    await session.commit()
    return service.shipment_out(shipment, payload)


@router.get("/shipments/{shipment_id}", response_model=ShipmentOut)
async def get_shipment(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> ShipmentOut:
    try:
        shipment, model = await service.load_shipment_model(session, shipment_id)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _check_org(shipment.org_id, ctx)
    draft = await service.latest_draft(session, shipment_id)
    return service.shipment_out(shipment, model, draft)


@router.put("/shipments/{shipment_id}", response_model=ShipmentOut)
async def update_shipment(
    shipment_id: uuid.UUID,
    payload: ShipmentIn,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> ShipmentOut:
    require_role(ctx, "declarant")
    try:
        shipment = await service.get_shipment(session, shipment_id)
        _check_org(shipment.org_id, ctx)
        shipment = await service.update_shipment(session, shipment_id, payload)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return service.shipment_out(shipment, payload)


@router.delete("/shipments/{shipment_id}", status_code=204)
async def delete_shipment(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> Response:
    require_role(ctx, "declarant")
    try:
        shipment = await service.get_shipment(session, shipment_id)
        _check_org(shipment.org_id, ctx)
        await service.delete_shipment(session, shipment_id)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return Response(status_code=204)


@router.post("/shipments/{shipment_id}/clone", response_model=ShipmentOut, status_code=201)
async def clone_shipment(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> ShipmentOut:
    require_role(ctx, "declarant")
    try:
        source = await service.get_shipment(session, shipment_id)
        _check_org(source.org_id, ctx)
        clone, model = await service.clone_shipment(session, shipment_id)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return service.shipment_out(clone, model)


@router.post(
    "/shipments/{shipment_id}/classify",
    response_model=ShipmentClassifyOut,
    status_code=202,
)
async def classify_shipment_items(
    shipment_id: uuid.UUID,
    force: bool = Query(False, description="Переклассифицировать все позиции "
                                           "и перезаписать коды"),
    session: AsyncSession = Depends(get_db),
    backend: JobBackend = Depends(get_job_backend),
    ctx: AuthContext = Depends(get_auth_context),
) -> ShipmentClassifyOut:
    require_role(ctx, "declarant")
    try:
        out = await classify_link.start_classification(
            session, ctx.org, shipment_id, force=force, backend=backend
        )
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NoItemsToClassify as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ShipmentClassifyOut(**out)


# ── Драфты ───────────────────────────────────────────────────────────────────

@router.post("/shipments/{shipment_id}/dt-draft", response_model=DtDraftOut, status_code=201)
async def generate_dt_draft(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> DtDraftOut:
    require_role(ctx, "declarant")
    try:
        shipment = await service.get_shipment(session, shipment_id)
        _check_org(shipment.org_id, ctx)
        draft = await service.generate_draft(session, shipment_id)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return draft


@router.get("/dt-drafts/{draft_id}", response_model=DtDraftOut)
async def get_dt_draft(
    draft_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> DtDraftOut:
    try:
        row = await service.get_draft(session, draft_id)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _check_org(row.org_id, ctx)
    return service.draft_out(row)


@router.get("/dt-drafts/{draft_id}/export.xlsx")
async def export_dt_draft_xlsx(
    draft_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> Response:
    try:
        row = await service.get_draft(session, draft_id)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _check_org(row.org_id, ctx)
    content = render_xlsx(row.graphs, list(row.validation_report or []))
    service.record_usage(
        session, org_id=row.org_id, kind="export",
        ref_type="dt_draft", ref_id=str(row.id), meta={"format": "xlsx"},
    )
    await session.commit()
    filename = f"dt_draft_v{row.version}_{str(row.shipment_id)[:8]}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/dt-drafts/{draft_id}/export.pdf")
async def export_dt_draft_pdf(
    draft_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> Response:
    try:
        row = await service.get_draft(session, draft_id)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _check_org(row.org_id, ctx)
    try:
        content = render_pdf(row.graphs, list(row.validation_report or []))
    except PdfFontError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    service.record_usage(
        session, org_id=row.org_id, kind="export",
        ref_type="dt_draft", ref_id=str(row.id), meta={"format": "pdf"},
    )
    await session.commit()
    filename = f"dt_draft_v{row.version}_{str(row.shipment_id)[:8]}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/dt-drafts/{draft_id}/export.xml")
async def export_dt_draft_xml(
    draft_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> Response:
    """XML-экспорт (структура R.055-draft; версия — DT_XML_STRUCTURE_VERSION).

    Если настроен DT_XML_XSD_PATH — документ валидируется по XSD,
    ошибки → 422 (файл не отдаётся).
    """
    try:
        row = await service.get_draft(session, draft_id)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _check_org(row.org_id, ctx)
    content = render_xml(row.graphs)
    xsd_errors = validate_against_xsd(content)
    if xsd_errors:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "XML не прошёл валидацию по XSD",
                "errors": xsd_errors[:20],
            },
        )
    service.record_usage(
        session, org_id=row.org_id, kind="export",
        ref_type="dt_draft", ref_id=str(row.id),
        meta={"format": "xml", "xsd_validated": xsd_errors is not None},
    )
    await session.commit()
    filename = f"dt_draft_v{row.version}_{str(row.shipment_id)[:8]}.xml"
    return Response(
        content=content,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Каталог товаров ──────────────────────────────────────────────────────────

def _catalog_out(row: ProductCatalogItem) -> CatalogItemOut:
    return CatalogItemOut(
        id=row.id, org_id=row.org_id, sku=row.sku, description=row.description,
        hs_code=row.hs_code, attributes=row.attributes or {},
        usage_count=row.usage_count, approved_by=row.approved_by,
        approved_at=row.approved_at, created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/catalog", response_model=CatalogListOut)
async def list_catalog(
    q: str | None = Query(None, description="Поиск по SKU и описанию"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> CatalogListOut:
    rows, total = await catalog.list_items(
        session, ctx.org_id, q=q, limit=limit, offset=offset
    )
    return CatalogListOut(items=[_catalog_out(r) for r in rows], total=total)


@router.get("/catalog/suggest", response_model=CatalogListOut)
async def suggest_catalog(
    q: str = Query(min_length=2),
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> CatalogListOut:
    rows = await catalog.suggest(session, ctx.org_id, q, limit=limit)
    return CatalogListOut(items=[_catalog_out(r) for r in rows], total=len(rows))


@router.post("/catalog", response_model=CatalogItemOut)
async def upsert_catalog_item(
    payload: CatalogItemIn,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> CatalogItemOut:
    require_role(ctx, "declarant")
    row, _created = await catalog.upsert_item(session, ctx.org_id, payload)
    await session.commit()
    await session.refresh(row)
    return _catalog_out(row)


@router.delete("/catalog/{item_id}", status_code=204)
async def delete_catalog_item(
    item_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> Response:
    require_role(ctx, "declarant")
    try:
        await catalog.delete_item(session, ctx.org_id, item_id)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return Response(status_code=204)


@router.post(
    "/catalog/import-from-shipment/{shipment_id}", response_model=CatalogImportOut
)
async def import_catalog_from_shipment(
    shipment_id: uuid.UUID,
    approved_by: str | None = Query(None, description="Кто утверждает коды (аудит)"),
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> CatalogImportOut:
    require_role(ctx, "declarant")
    try:
        out = await catalog.import_from_shipment(
            session, ctx.org_id, shipment_id,
            approved_by=approved_by or (ctx.account.email if ctx.account else None),
        )
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return out


# ── Документы досье (Фаза 2, SGR-слой) ───────────────────────────────────────

async def _own_shipment(
    session: AsyncSession, shipment_id: uuid.UUID, ctx: AuthContext
):
    """Досье своей организации или 404."""
    try:
        shipment = await service.get_shipment(session, shipment_id)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _check_org(shipment.org_id, ctx)
    return shipment


@router.get("/shipments/{shipment_id}/documents", response_model=DocumentListOut)
async def list_shipment_documents(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> DocumentListOut:
    await _own_shipment(session, shipment_id, ctx)
    rows = await documents.list_documents(session, shipment_id)
    return DocumentListOut(
        items=[documents.document_out(r) for r in rows], total=len(rows)
    )


@router.post(
    "/shipments/{shipment_id}/documents", response_model=DocumentOut, status_code=201
)
async def attach_shipment_document(
    shipment_id: uuid.UUID,
    payload: DocumentIn,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> DocumentOut:
    require_role(ctx, "declarant")
    await _own_shipment(session, shipment_id, ctx)
    try:
        row = await documents.attach_document(
            session, shipment_id, payload, org_id=ctx.org_id,
        )
    except DocumentSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return documents.document_out(row)


@router.delete(
    "/shipments/{shipment_id}/documents/{doc_id}", status_code=204
)
async def delete_shipment_document(
    shipment_id: uuid.UUID,
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> Response:
    require_role(ctx, "declarant")
    await _own_shipment(session, shipment_id, ctx)
    try:
        await documents.delete_document(session, shipment_id, doc_id)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return Response(status_code=204)


@router.post(
    "/shipments/{shipment_id}/documents/{doc_id}/extract",
    response_model=DocumentOut,
)
async def extract_shipment_document(
    shipment_id: uuid.UUID,
    doc_id: uuid.UUID,
    kind: str | None = Query(
        None,
        description="Явный тип документа (после needs_kind): "
                    "invoice | packing | cmr | contract | cert_origin",
    ),
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> DocumentOut:
    """Типизация (LLM, уровень 90) + извлечение полей (уровень 91) + пересчёт кодом."""
    require_role(ctx, "declarant")
    await _own_shipment(session, shipment_id, ctx)
    if kind is not None and kind not in EXTRACTABLE_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"Нет экстрактора для типа «{kind}». "
                   f"Доступны: {', '.join(EXTRACTABLE_KINDS)}",
        )
    try:
        row = await documents.extract_document_fields(
            session, shipment_id, doc_id, kind_override=kind
        )
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DocumentSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    service.record_usage(
        session, org_id=ctx.org_id, kind="dt_extract",
        ref_type="shipment_document", ref_id=str(doc_id),
        meta={"doc_kind": row.kind, "status": (row.extracted or {}).get("status")},
    )
    await session.commit()
    return documents.document_out(row)


@router.post(
    "/shipments/{shipment_id}/documents/{doc_id}/verify-permit",
    response_model=PermitCheckOut,
)
async def verify_permit_document(
    shipment_id: uuid.UUID,
    doc_id: uuid.UUID,
    force: bool = Query(False, description="Игнорировать кэш (TULPAR_CACHE_HOURS)"),
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> PermitCheckOut:
    """Проверка разрешительного: формат номера + поиск по реестрам TULPAR.

    Без TULPAR_SEARCH_URL работает только детерминированная проверка формата.
    Результат сохраняется и попадает в графу 44 (permit_status) и W-TULPAR.
    """
    require_role(ctx, "declarant")
    await _own_shipment(session, shipment_id, ctx)
    try:
        result, _row = await tulpar.verify_permit(
            session, shipment_id, doc_id, force=force
        )
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except tulpar.PermitKindError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Метерим только реальные обращения к реестру (кэш и формат бесплатны).
    if result.source == "registry" and not result.cached:
        service.record_usage(
            session, org_id=ctx.org_id, kind="tulpar_check",
            ref_type="shipment_document", ref_id=str(doc_id),
            meta={"status": result.status},
        )
    await session.commit()
    return PermitCheckOut(
        document_id=doc_id,
        status=result.status,
        detail=result.detail,
        source=result.source,
        checked_at=result.checked_at,
        cached=result.cached,
    )


# ── Кросс-сверка, графа 31, советник, fixer (Фаза 2) ─────────────────────────

@router.post("/shipments/{shipment_id}/crosscheck", response_model=CrosscheckOut)
async def crosscheck_shipment(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> CrosscheckOut:
    """Числа сверяет код (X-01..X-06), сущности нечётко сопоставляет LLM."""
    require_role(ctx, "declarant")
    await _own_shipment(session, shipment_id, ctx)
    _, model = await service.load_shipment_model(session, shipment_id)
    doc_rows = await documents.list_documents(session, shipment_id)
    payloads = documents.extracted_payloads(doc_rows)
    if not payloads:
        raise HTTPException(
            status_code=409,
            detail="У досье нет документов с извлечёнными данными — "
                   "сначала выполните extract.",
        )
    out = await crosscheck.run_crosscheck(model, payloads)
    if out.llm_used:
        service.record_usage(
            session, org_id=ctx.org_id, kind="dt_crosscheck",
            ref_type="shipment", ref_id=str(shipment_id),
            meta={"docs": len(payloads)},
        )
        await session.commit()
    return out


@router.post(
    "/shipments/{shipment_id}/items/{position}/describe31", response_model=G31Out
)
async def describe_g31(
    shipment_id: uuid.UUID,
    position: int,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> G31Out:
    """Предложить текст графы 31 (grounding-валидатор, только предложение)."""
    require_role(ctx, "declarant")
    await _own_shipment(session, shipment_id, ctx)
    _, model = await service.load_shipment_model(session, shipment_id)
    if position < 1 or position > len(model.items):
        raise HTTPException(
            status_code=404, detail=f"Позиция {position} не найдена в досье"
        )
    item = model.items[position - 1]

    catalog_attributes: dict | None = None
    if item.sku:
        entry = await catalog.get_by_sku(session, ctx.org_id, item.sku)
        if entry is not None:
            catalog_attributes = entry.attributes or {}

    doc_rows = await documents.list_documents(session, shipment_id)
    out = await describe31.generate_g31(
        item,
        catalog_attributes=catalog_attributes,
        docs=documents.extracted_payloads(doc_rows),
    )
    service.record_usage(
        session, org_id=ctx.org_id, kind="dt_g31",
        ref_type="shipment_item", ref_id=f"{shipment_id}:{position}",
        meta={"accepted": out.accepted, "attempts": out.attempts},
    )
    await session.commit()
    return out


@router.post("/shipments/{shipment_id}/advise", response_model=AdviseOut)
async def advise_shipment(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> AdviseOut:
    """Риск-советник: сигналы считает код, LLM формулирует + проверка 31↔33."""
    require_role(ctx, "declarant")
    await _own_shipment(session, shipment_id, ctx)
    _, model = await service.load_shipment_model(session, shipment_id)
    code_names = await advisor.load_code_names(session, model)
    out = await advisor.advise(model, code_names)
    if out.llm_used:
        service.record_usage(
            session, org_id=ctx.org_id, kind="dt_advise",
            ref_type="shipment", ref_id=str(shipment_id),
            meta={"signals": len(out.signals)},
        )
        await session.commit()
    return out


@router.post("/dt-drafts/{draft_id}/fix-suggestions", response_model=FixOut)
async def suggest_draft_fixes(
    draft_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> FixOut:
    """Предложения правок по findings драфта; каждое проверено повторным ФЛК.

    Findings берутся из указанной версии драфта, досье — текущее: fixer
    работает с тем, что человек будет править. Применяет человек (PUT досье).
    """
    require_role(ctx, "declarant")
    try:
        row = await service.get_draft(session, draft_id)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _check_org(row.org_id, ctx)

    _, model = await service.load_shipment_model(session, row.shipment_id)
    doc_rows = await documents.list_documents(session, row.shipment_id)
    findings = [Finding(**f) for f in (row.validation_report or [])]
    out = await fixer.suggest_fixes(
        session, model, findings,
        shipment_ref=str(row.shipment_id),
        doc_payloads=documents.extracted_payloads(doc_rows),
        documents_g44=documents.build_g44_entries(doc_rows),
    )
    if out.llm_used:
        service.record_usage(
            session, org_id=ctx.org_id, kind="dt_fix",
            ref_type="dt_draft", ref_id=str(draft_id),
            meta={"suggestions": len(out.suggestions)},
        )
        await session.commit()
    return out
