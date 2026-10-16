"""Оркестрация ДТ-ядра: организации, досье, генерация и выдача драфтов.

Слой между API-роутером и чистыми модулями (assembler / flk / refdata).
Правки досье и генерация драфта — здесь; расчёты — только в чистых модулях.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from feap.clock import today_bishkek
from feap.config import settings
from feap.db.models import (
    DtDraftRow,
    Organization,
    Shipment,
    ShipmentItemRow,
)
from feap.dt import catalog, crosscheck, documents
from feap.dt.assembler import STRUCTURE_VERSION, assemble
from feap.dt.errors import NotFoundError
from feap.dt.flk import draft_status, validate_draft
from feap.dt.refdata import build_ref_snapshot
from feap.dt.schemas import (
    AssembledDraft,
    DtDraftOut,
    Finding,
    ShipmentIn,
    ShipmentListEntry,
    ShipmentOut,
)
from feap.orgs import DEFAULT_ORG_NAME, resolve_org
from feap.tariff.fees import FeeRule, kg_release_fee_rule
from feap.usage import record_usage

# Реэкспорт для обратной совместимости: исторические точки входа модуля.
__all__ = ["NotFoundError", "DEFAULT_ORG_NAME", "resolve_org", "record_usage"]


# ── Досье ────────────────────────────────────────────────────────────────────

def _shipment_rows(payload: ShipmentIn, shipment: Shipment) -> list[ShipmentItemRow]:
    return [
        ShipmentItemRow(
            shipment_id=shipment.id,
            position=idx,
            data=item.model_dump(mode="json"),
            hs_code=item.hs_code,
        )
        for idx, item in enumerate(payload.items, start=1)
    ]


async def create_shipment(
    session: AsyncSession, org: Organization, payload: ShipmentIn
) -> Shipment:
    await catalog.apply_catalog_codes(session, org.id, payload)
    shipment = Shipment(
        org_id=org.id,
        status="draft",
        procedure=payload.procedure,
        data=payload.model_dump(mode="json", exclude={"items"}),
    )
    session.add(shipment)
    await session.flush()
    for row in _shipment_rows(payload, shipment):
        session.add(row)
    await session.flush()
    return shipment


async def get_shipment(session: AsyncSession, shipment_id: uuid.UUID) -> Shipment:
    result = await session.execute(
        select(Shipment).where(Shipment.id == shipment_id)
    )
    shipment = result.scalar_one_or_none()
    if shipment is None:
        raise NotFoundError(f"Досье {shipment_id} не найдено")
    return shipment


async def get_shipment_items(
    session: AsyncSession, shipment_id: uuid.UUID
) -> list[ShipmentItemRow]:
    result = await session.execute(
        select(ShipmentItemRow)
        .where(ShipmentItemRow.shipment_id == shipment_id)
        .order_by(ShipmentItemRow.position)
    )
    return list(result.scalars().all())


async def update_shipment(
    session: AsyncSession, shipment_id: uuid.UUID, payload: ShipmentIn
) -> Shipment:
    shipment = await get_shipment(session, shipment_id)
    await catalog.apply_catalog_codes(session, shipment.org_id, payload)
    shipment.procedure = payload.procedure
    shipment.data = payload.model_dump(mode="json", exclude={"items"})
    # Позиции живут в shipment_items: PUT только позиций не трогает строку
    # shipments, и onupdate не срабатывает. Двигаем явно — на updated_at
    # опираются сортировка списка и «проект ДТ устарел» на фронте.
    shipment.updated_at = func.now()
    # Позиции заменяются целиком (простая и предсказуемая семантика PUT).
    for row in await get_shipment_items(session, shipment_id):
        await session.delete(row)
    await session.flush()
    for row in _shipment_rows(payload, shipment):
        session.add(row)
    await session.flush()
    await session.refresh(shipment, attribute_names=["updated_at"])
    return shipment


async def delete_shipment(session: AsyncSession, shipment_id: uuid.UUID) -> None:
    shipment = await get_shipment(session, shipment_id)
    await session.delete(shipment)


async def clone_shipment(
    session: AsyncSession, shipment_id: uuid.UUID
) -> tuple[Shipment, ShipmentIn]:
    """Копия досье: шапка + позиции (с кодами). Драфты и документы не копируются."""
    source, model = await load_shipment_model(session, shipment_id)
    clone = Shipment(
        org_id=source.org_id,
        status="draft",
        procedure=source.procedure,
        data=dict(source.data),
    )
    session.add(clone)
    await session.flush()
    for row in _shipment_rows(model, clone):
        session.add(row)
    await session.flush()
    return clone, model


async def list_shipments(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ShipmentListEntry], int]:
    base = select(Shipment).where(Shipment.org_id == org_id)
    if status:
        base = base.where(Shipment.status == status)
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    items_total = func.count(ShipmentItemRow.id)
    items_no_code = func.count(ShipmentItemRow.id).filter(ShipmentItemRow.hs_code.is_(None))
    stmt = (
        select(Shipment, items_total, items_no_code)
        .outerjoin(ShipmentItemRow, ShipmentItemRow.shipment_id == Shipment.id)
        .where(Shipment.org_id == org_id)
        .group_by(Shipment.id)
        .order_by(Shipment.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        stmt = stmt.where(Shipment.status == status)

    entries = [
        ShipmentListEntry(
            id=shipment.id,
            status=shipment.status,
            procedure=shipment.procedure,
            buyer_name=(shipment.data.get("buyer") or {}).get("name"),
            items_count=n_items,
            items_without_code=n_no_code,
            created_at=shipment.created_at,
            updated_at=shipment.updated_at,
        )
        for shipment, n_items, n_no_code in (await session.execute(stmt)).all()
    ]
    return entries, total


async def load_shipment_model(
    session: AsyncSession, shipment_id: uuid.UUID
) -> tuple[Shipment, ShipmentIn]:
    shipment = await get_shipment(session, shipment_id)
    items = await get_shipment_items(session, shipment_id)
    model = ShipmentIn(**{**shipment.data, "items": [row.data for row in items]})
    return shipment, model


def shipment_out(
    shipment: Shipment, model: ShipmentIn,
    latest_draft: DtDraftRow | None = None,
) -> ShipmentOut:
    return ShipmentOut(
        id=shipment.id,
        org_id=shipment.org_id,
        status=shipment.status,
        data=model,
        created_at=shipment.created_at,
        updated_at=shipment.updated_at,
        latest_draft_id=latest_draft.id if latest_draft else None,
        latest_draft_version=latest_draft.version if latest_draft else None,
    )


async def latest_draft(
    session: AsyncSession, shipment_id: uuid.UUID
) -> DtDraftRow | None:
    result = await session.execute(
        select(DtDraftRow)
        .where(DtDraftRow.shipment_id == shipment_id)
        .order_by(DtDraftRow.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# ── Генерация драфта ─────────────────────────────────────────────────────────

def _configured_fee() -> Decimal | None:
    """Фиксированный сбор (режим fixed); None — сбор считается правилом или выключен."""
    if settings.DT_CUSTOMS_FEE_MODE != "fixed":
        return None
    raw = (settings.DT_CUSTOMS_FEE_KGS or "").strip()
    try:
        return Decimal(raw) if raw else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _configured_fee_rule() -> FeeRule | None:
    if settings.DT_CUSTOMS_FEE_MODE != "percent":
        return None
    try:
        rp = Decimal((settings.DT_CALC_INDEX_KGS or "100").strip())
    except InvalidOperation:
        rp = Decimal(100)
    return kg_release_fee_rule(rp)


async def assemble_and_validate(
    session: AsyncSession,
    model: ShipmentIn,
    *,
    shipment_ref: str | None = None,
    doc_payloads: list[dict] | None = None,
    documents_g44: list | None = None,
) -> tuple[AssembledDraft, list[Finding]]:
    """Сборка граф + ФЛК + числовая кросс-сверка с документами, без персиста.

    Единая точка правды для generate_draft и верификации fixer'а —
    предложение исправления проверяется ровно тем же прогоном.
    documents_g44 — list[assembler.DocEntry] для графы 44.
    """
    rate_date = model.rate_date or today_bishkek()
    snapshot = await build_ref_snapshot(
        session,
        hs_codes=[i.hs_code for i in model.items],
        contract_currency=model.contract_currency,
        rate_date=rate_date,
        manual_rates=model.manual_fx_rates,
    )
    assert snapshot.rates is not None

    refdata = snapshot.refdata_meta()
    refdata["guard_mode"] = settings.REFDATA_GUARD
    assembled = assemble(
        model, snapshot.item_refs, snapshot.rates,
        fee_kgs=_configured_fee(),
        fee_rule=_configured_fee_rule(),
        shipment_ref=shipment_ref,
        documents=documents_g44,
        remedies=snapshot.remedies,
        agreements=snapshot.agreements,
        vat_includes_remedies=settings.DT_VAT_INCLUDE_REMEDY_DUTIES,
        refdata=refdata,
    )
    # Нет гарантии свежести справочника (сверка с ЕЭК просрочена) — суммы не
    # отдаются: то же needs_input, что и при нехватке данных досье. В режиме
    # warn (локальная разработка) — предупреждение W-REF-GUARD в ФЛК.
    if settings.REFDATA_GUARD == "enforce":
        assembled.missing_parameters.extend(refdata["blocking"])
    findings = validate_draft(assembled.graphs, model, snapshot)
    if doc_payloads:
        # Извлечённые документы — уже данные (JSONB), сверка детерминирована.
        findings.extend(crosscheck.numeric_findings(model, doc_payloads))
    return assembled, findings


async def generate_draft(
    session: AsyncSession, shipment_id: uuid.UUID
) -> DtDraftOut:
    shipment, model = await load_shipment_model(session, shipment_id)
    doc_rows = await documents.list_documents(session, shipment_id)
    assembled, findings = await assemble_and_validate(
        session, model,
        shipment_ref=str(shipment.id),
        doc_payloads=documents.extracted_payloads(doc_rows),
        documents_g44=documents.build_g44_entries(doc_rows),
    )
    status = draft_status(findings, assembled.missing_parameters)

    next_version = (
        await session.execute(
            select(func.coalesce(func.max(DtDraftRow.version), 0))
            .where(DtDraftRow.shipment_id == shipment_id)
        )
    ).scalar_one() + 1

    row = DtDraftRow(
        shipment_id=shipment.id,
        org_id=shipment.org_id,
        version=next_version,
        status=status,
        structure_version=STRUCTURE_VERSION,
        graphs=assembled.graphs,
        validation_report=[f.model_dump(mode="json") for f in findings],
        missing_parameters=assembled.missing_parameters,
    )
    session.add(row)
    shipment.status = "review" if status != "ready" else "ready"
    record_usage(
        session, org_id=shipment.org_id, kind="dt_draft",
        ref_type="dt_draft", ref_id=str(shipment.id),
        meta={"items": len(model.items), "status": status},
    )
    await session.flush()
    return draft_out(row)


async def get_draft(session: AsyncSession, draft_id: uuid.UUID) -> DtDraftRow:
    row = await session.get(DtDraftRow, draft_id)
    if row is None:
        raise NotFoundError(f"Драфт {draft_id} не найден")
    return row


def draft_out(row: DtDraftRow) -> DtDraftOut:
    return DtDraftOut(
        id=row.id,
        shipment_id=row.shipment_id,
        version=row.version,
        status=row.status,  # type: ignore[arg-type]
        structure_version=row.structure_version,
        graphs=row.graphs,
        findings=[Finding(**f) for f in (row.validation_report or [])],
        missing_parameters=list(row.missing_parameters or []),
        created_at=row.created_at,
    )
