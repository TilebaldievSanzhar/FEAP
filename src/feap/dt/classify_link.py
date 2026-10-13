"""Связка досье ↔ каскадный классификатор.

Запуск: позиции досье без кода уходят стандартным batch-job'ом
(job_type="shipment_classify", каждая позиция несёт source_ref на свою
строку shipment_items). Обратная запись: когда item джобы завершается,
worker вызывает apply_item_result — код ложится в позицию досье.

LLM здесь по-прежнему нет: модуль только перекладывает данные между
джобой классификации и строками досье.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Iterable, Protocol

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from feap.db.models import ClassificationJob, Organization, Shipment, ShipmentItemRow
from feap.dt.errors import NoItemsToClassify, NotFoundError
from feap.jobs import repository as jobs_repo
from feap.jobs.schemas import BatchItemInput

logger = logging.getLogger(__name__)

JOB_TYPE = "shipment_classify"
#: Статусы джобы, при которых досье возвращается из "classifying".
#: needs_input не финализирует: позиции ещё ждут ответов пользователя.
FINAL_JOB_STATUSES = ("completed", "partial", "failed")


class _EnqueueBackend(Protocol):
    async def enqueue_batch(self, job_id: uuid.UUID) -> None: ...


# ── Чистые функции (тестируются без БД) ──────────────────────────────────────

def normalize_code(final_code: str | None) -> str | None:
    """'8509 80 000 0' → '8509800000'; всё, что не даёт ровно 10 цифр — None."""
    if not final_code:
        return None
    digits = re.sub(r"\D", "", final_code)
    return digits if len(digits) == 10 else None


def split_candidates(
    rows: Iterable[ShipmentItemRow], *, force: bool
) -> tuple[list[ShipmentItemRow], int]:
    """Позиции для классификации и число пропущенных (код уже проставлен)."""
    rows = list(rows)
    candidates = [r for r in rows if force or not r.hs_code]
    return candidates, len(rows) - len(candidates)


def build_batch_items(
    candidates: Iterable[ShipmentItemRow], *, shipment_id: uuid.UUID, force: bool
) -> list[BatchItemInput]:
    items: list[BatchItemInput] = []
    for row in candidates:
        data = row.data or {}
        additional: dict[str, str] = {}
        if data.get("origin_country"):
            additional["страна происхождения"] = str(data["origin_country"])
        items.append(
            BatchItemInput(
                description=str(data.get("description") or "").strip() or "—",
                additional_info=additional,
                source_ref={
                    "shipment_id": str(shipment_id),
                    "shipment_item_id": str(row.id),
                    "position": row.position,
                    "overwrite": force,
                },
            )
        )
    return items


# ── Запуск классификации ─────────────────────────────────────────────────────

async def start_classification(
    session: AsyncSession,
    org: Organization,
    shipment_id: uuid.UUID,
    *,
    force: bool = False,
    backend: _EnqueueBackend,
) -> dict[str, Any]:
    """Создать batch-job по позициям досье и поставить его в очередь.

    Возвращает данные для ShipmentClassifyOut. force=True переклассифицирует
    все позиции и разрешает перезапись уже проставленных кодов.
    """
    from feap.dt import service  # локальный импорт: service ← catalog, без цикла

    shipment = await service.get_shipment(session, shipment_id)
    if shipment.org_id is not None and shipment.org_id != org.id:
        raise NotFoundError(f"Досье {shipment_id} не найдено")
    rows = await service.get_shipment_items(session, shipment_id)

    candidates, skipped = split_candidates(rows, force=force)
    if not candidates:
        raise NoItemsToClassify(
            "Все позиции досье уже имеют код ТН ВЭД; используйте force=true "
            "для переклассификации"
        )

    batch_items = build_batch_items(candidates, shipment_id=shipment_id, force=force)
    job = await jobs_repo.create_job(
        session,
        job_type=JOB_TYPE,
        items=batch_items,
        params={
            "shipment_id": str(shipment_id),
            "org_id": str(shipment.org_id) if shipment.org_id else None,
        },
        org_id=shipment.org_id,
    )

    shipment.status = "classifying"
    service.record_usage(
        session,
        org_id=shipment.org_id,
        kind="classification",
        quantity=len(batch_items),
        ref_type="classification_job",
        ref_id=str(job.id),
        meta={"shipment_id": str(shipment_id), "force": force},
    )
    await session.commit()
    await backend.enqueue_batch(job.id)

    return {
        "job_id": job.id,
        "status": job.status,
        "enqueued": len(batch_items),
        "skipped": skipped,
        "poll_url": f"/api/v1/jobs/{job.id}",
        "items_url": f"/api/v1/jobs/{job.id}/items",
        "events_url": f"/api/v1/jobs/{job.id}/events",
    }


# ── Обратная запись (вызывается из worker'а) ─────────────────────────────────

async def apply_item_result(
    session: AsyncSession,
    *,
    job_item_id: uuid.UUID,
    source_ref: dict[str, Any],
    result_json: dict[str, Any],
) -> bool:
    """Записать код завершённого item'а в позицию досье.

    Ручной код не перезаписывается, если джоба не была запущена с force
    (overwrite в source_ref) — пользователь мог проставить код, пока джоба шла.
    """
    code = normalize_code(result_json.get("final_code"))
    if code is None:
        logger.warning(
            "Item %s: final_code %r не сводится к 10 цифрам — пропуск записи",
            job_item_id, result_json.get("final_code"),
        )
        return False

    try:
        row_id = uuid.UUID(str(source_ref.get("shipment_item_id")))
    except ValueError:
        return False
    row = await session.get(ShipmentItemRow, row_id)
    if row is None:
        logger.warning("Item %s: позиция досье %s уже удалена", job_item_id, row_id)
        return False
    if row.hs_code and not source_ref.get("overwrite"):
        return False

    data = dict(row.data or {})
    data["hs_code"] = code
    data["classification_ref"] = f"job_item:{job_item_id}"
    extra = dict(data.get("extra") or {})
    extra["classification"] = {
        "final_code": result_json.get("final_code"),
        "code_description": result_json.get("code_description"),
        "confidence": result_json.get("confidence"),
    }
    data["extra"] = extra
    row.data = data
    row.hs_code = code
    await session.commit()
    return True


async def finalize_shipment_job(
    session: AsyncSession, job: ClassificationJob, new_status: str
) -> None:
    """Вернуть досье из 'classifying' в 'draft', когда джоба финализировалась."""
    if job.job_type != JOB_TYPE or new_status not in FINAL_JOB_STATUSES:
        return
    raw = (job.params or {}).get("shipment_id")
    if not raw:
        return
    await session.execute(
        update(Shipment)
        .where(Shipment.id == uuid.UUID(raw), Shipment.status == "classifying")
        .values(status="draft")
    )
    await session.commit()
