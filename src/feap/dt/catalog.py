"""Каталог товаров компании: память «артикул → утверждённый код».

Автоподстановка работает только из каталога (человеком утверждённые коды);
результаты LLM-классификации попадают сюда исключительно через явный
import_from_shipment — это и есть акт утверждения.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Protocol

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from feap.db.models import ProductCatalogItem, Shipment, ShipmentItemRow
from feap.dt.errors import NotFoundError
from feap.dt.schemas import CatalogImportOut, CatalogItemIn, ShipmentIn, ShipmentItemIn


class _CatalogEntry(Protocol):
    id: uuid.UUID
    hs_code: str | None


# ── Чистая логика подстановки (тестируется без БД) ──────────────────────────

def substitute_codes(
    items: Iterable[ShipmentItemIn], by_sku: Mapping[str, _CatalogEntry]
) -> list[uuid.UUID]:
    """Проставить коды из каталога позициям без кода. Возвращает id использованных записей.

    Уже заполненный hs_code не трогаем — каталог дополняет, а не перезаписывает.
    """
    used: list[uuid.UUID] = []
    for item in items:
        if item.hs_code or not item.sku:
            continue
        entry = by_sku.get(item.sku)
        if entry is None or not entry.hs_code:
            continue
        item.hs_code = entry.hs_code
        item.classification_ref = f"catalog:{entry.id}"
        used.append(entry.id)
    return used


# ── Автоподстановка при создании/обновлении досье ────────────────────────────

async def apply_catalog_codes(
    session: AsyncSession, org_id: uuid.UUID | None, payload: ShipmentIn
) -> int:
    """Подставить утверждённые коды по SKU; инкрементировать usage_count."""
    if org_id is None:
        return 0
    skus = {i.sku for i in payload.items if i.sku and not i.hs_code}
    if not skus:
        return 0

    result = await session.execute(
        select(ProductCatalogItem).where(
            ProductCatalogItem.org_id == org_id,
            ProductCatalogItem.sku.in_(skus),
            ProductCatalogItem.hs_code.is_not(None),
        )
    )
    by_sku = {row.sku: row for row in result.scalars()}
    used = substitute_codes(payload.items, by_sku)
    for catalog_id, times in Counter(used).items():
        await session.execute(
            update(ProductCatalogItem)
            .where(ProductCatalogItem.id == catalog_id)
            .values(usage_count=ProductCatalogItem.usage_count + times)
        )
    return len(used)


# ── CRUD ─────────────────────────────────────────────────────────────────────

async def list_items(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ProductCatalogItem], int]:
    base = select(ProductCatalogItem).where(ProductCatalogItem.org_id == org_id)
    if q:
        pattern = f"%{q}%"
        base = base.where(or_(
            ProductCatalogItem.sku.ilike(pattern),
            ProductCatalogItem.description.ilike(pattern),
        ))
    total = (
        await session.execute(
            select(func.count()).select_from(base.subquery())
        )
    ).scalar_one()
    rows = await session.execute(
        base.order_by(ProductCatalogItem.usage_count.desc(),
                      ProductCatalogItem.updated_at.desc())
        .limit(limit).offset(offset)
    )
    return list(rows.scalars().all()), total


async def suggest(
    session: AsyncSession, org_id: uuid.UUID, q: str, *, limit: int = 10
) -> list[ProductCatalogItem]:
    """Подсказки для позиции досье: только записи с утверждённым кодом."""
    pattern = f"%{q}%"
    result = await session.execute(
        select(ProductCatalogItem)
        .where(
            ProductCatalogItem.org_id == org_id,
            ProductCatalogItem.hs_code.is_not(None),
            or_(
                ProductCatalogItem.sku.ilike(pattern),
                ProductCatalogItem.description.ilike(pattern),
            ),
        )
        .order_by(ProductCatalogItem.usage_count.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_by_sku(
    session: AsyncSession, org_id: uuid.UUID | None, sku: str
) -> ProductCatalogItem | None:
    """Запись каталога по точному SKU (для фактов графы 31 и подстановок)."""
    if org_id is None:
        return None
    result = await session.execute(
        select(ProductCatalogItem).where(
            ProductCatalogItem.org_id == org_id,
            ProductCatalogItem.sku == sku,
        )
    )
    return result.scalar_one_or_none()


async def upsert_item(
    session: AsyncSession, org_id: uuid.UUID, payload: CatalogItemIn
) -> tuple[ProductCatalogItem, bool]:
    """Создать или обновить запись по (org, sku). Возвращает (row, created)."""
    result = await session.execute(
        select(ProductCatalogItem).where(
            ProductCatalogItem.org_id == org_id,
            ProductCatalogItem.sku == payload.sku,
        )
    )
    row = result.scalar_one_or_none()
    now = dt.datetime.now(dt.timezone.utc)
    if row is None:
        row = ProductCatalogItem(
            org_id=org_id,
            sku=payload.sku,
            description=payload.description,
            hs_code=payload.hs_code,
            attributes=payload.attributes,
            approved_by=payload.approved_by,
            approved_at=now if payload.hs_code else None,
        )
        session.add(row)
        await session.flush()
        return row, True

    code_changed = payload.hs_code is not None and payload.hs_code != row.hs_code
    row.description = payload.description
    if payload.attributes:  # пустой dict не затирает накопленные атрибуты
        row.attributes = payload.attributes
    if payload.hs_code is not None:
        row.hs_code = payload.hs_code
    if code_changed:
        row.approved_by = payload.approved_by
        row.approved_at = now
    elif payload.approved_by:
        row.approved_by = payload.approved_by
    await session.flush()
    return row, False


async def delete_item(
    session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID
) -> None:
    row = await session.get(ProductCatalogItem, item_id)
    if row is None or row.org_id != org_id:
        raise NotFoundError(f"Запись каталога {item_id} не найдена")
    await session.delete(row)


# ── Импорт утверждённых кодов из досье ───────────────────────────────────────

async def import_from_shipment(
    session: AsyncSession,
    org_id: uuid.UUID,
    shipment_id: uuid.UUID,
    *,
    approved_by: str | None = None,
) -> CatalogImportOut:
    """Перенести позиции досье (SKU + код) в каталог — явное утверждение кодов."""
    shipment = await session.get(Shipment, shipment_id)
    if shipment is None or (shipment.org_id is not None and shipment.org_id != org_id):
        raise NotFoundError(f"Досье {shipment_id} не найдено")

    result = await session.execute(
        select(ShipmentItemRow)
        .where(ShipmentItemRow.shipment_id == shipment_id)
        .order_by(ShipmentItemRow.position)
    )
    imported = updated = skipped = 0
    for row in result.scalars():
        data = row.data or {}
        sku, code = data.get("sku"), row.hs_code
        if not sku or not code:
            skipped += 1
            continue
        payload = CatalogItemIn(
            sku=sku,
            description=str(data.get("description") or sku),
            hs_code=code,
            attributes={},
            approved_by=approved_by,
        )
        _, created = await upsert_item(session, org_id, payload)
        if created:
            imported += 1
        else:
            updated += 1
    return CatalogImportOut(imported=imported, updated=updated, skipped=skipped)
