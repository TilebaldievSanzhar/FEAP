"""Документы досье: привязка, извлечение (SGR), выдача.

Текст документа хранится в shipment_documents.extracted JSONB:
    {
      "source": {"origin": "text"|"ocr", "ocr_job_id", "file_index", "text"},
      "status": "attached" | "extracted" | "needs_kind",
      "kind", "doc_type", "doc", "checks", "sensor_warnings", "extracted_at"
    }
Extraction-результаты складываются сюда же — это trace и будущий датасет
(dt_core_dev_plan.md §4). Отдельной таблицы и миграции не требуется.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from feap.db.models import OcrJob, ShipmentDocumentRow
from feap.dt.assembler import DocEntry
from feap.dt.errors import NotFoundError
from feap.dt.extractors import extract as extractors
from feap.dt.schemas import DocumentIn, DocumentOut

#: Значение колонки kind, пока тип не определён (в БД нет 'unknown'/'auto').
_KIND_PLACEHOLDER = "other"

#: kind → код вида документа для графы 44 (Решение 378, прил. 8).
#: TODO(verify): сверить с актуальной редакцией и реальной ДТ КР
#: (docs/dt_draft_spec.md §12.3); те же значения — в ref_classifiers
#: (classifier_code='doc_kinds', verified=false). Типы без кода
#: (packing/permit/other) попадают в графу 44 без кода — проставит человек.
DOC_KIND_CODES: dict[str, str] = {
    "contract": "03011",
    "invoice": "04021",
    "cmr": "02015",
    "awb": "02016",
    "rail": "02013",
    "cert_origin": "06011",
}


class DocumentSourceError(ValueError):
    """Источник текста недоступен: OCR-job не найден/не готов/индекс вне диапазона."""


async def _resolve_text(
    session: AsyncSession, payload: DocumentIn, org_id: uuid.UUID | None = None,
) -> tuple[str, dict]:
    """Достать текст из payload или из завершённого OCR-job'а.

    OCR-задача чужой организации неотличима от несуществующей.
    """
    if payload.text is not None:
        return payload.text, {"origin": "text"}

    job = await session.get(OcrJob, payload.ocr_job_id)
    owner = getattr(job, "org_id", None) if job is not None else None
    if job is None or (org_id is not None and owner is not None and owner != org_id):
        raise DocumentSourceError(f"OCR-задача {payload.ocr_job_id} не найдена")
    extraction = list(job.extraction or [])
    if not extraction:
        raise DocumentSourceError(
            f"OCR-задача {payload.ocr_job_id} ещё не содержит извлечённого текста "
            f"(статус: {job.status})"
        )
    if payload.file_index >= len(extraction):
        raise DocumentSourceError(
            f"file_index={payload.file_index} вне диапазона: в задаче "
            f"{len(extraction)} файлов"
        )
    entry = extraction[payload.file_index]
    text = (entry.get("text") or "").strip()
    if not text:
        raise DocumentSourceError(
            f"Файл №{payload.file_index} ({entry.get('filename')}) дал пустой OCR-текст"
        )
    return text, {
        "origin": "ocr",
        "ocr_job_id": str(payload.ocr_job_id),
        "file_index": payload.file_index,
        "filename": entry.get("filename"),
    }


async def attach_document(
    session: AsyncSession, shipment_id: uuid.UUID, payload: DocumentIn,
    org_id: uuid.UUID | None = None,
) -> ShipmentDocumentRow:
    text, source = await _resolve_text(session, payload, org_id)
    source["text"] = text
    row = ShipmentDocumentRow(
        shipment_id=shipment_id,
        kind=payload.kind if payload.kind != "auto" else _KIND_PLACEHOLDER,
        ocr_job_id=payload.ocr_job_id,
        number=payload.number,
        doc_date=payload.doc_date,
        extracted={"source": source, "status": "attached"},
    )
    session.add(row)
    await session.flush()
    return row


async def list_documents(
    session: AsyncSession, shipment_id: uuid.UUID
) -> list[ShipmentDocumentRow]:
    result = await session.execute(
        select(ShipmentDocumentRow)
        .where(ShipmentDocumentRow.shipment_id == shipment_id)
        .order_by(ShipmentDocumentRow.created_at)
    )
    return list(result.scalars().all())


async def get_document(
    session: AsyncSession, shipment_id: uuid.UUID, doc_id: uuid.UUID
) -> ShipmentDocumentRow:
    row = await session.get(ShipmentDocumentRow, doc_id)
    if row is None or row.shipment_id != shipment_id:
        raise NotFoundError(f"Документ {doc_id} не найден в досье {shipment_id}")
    return row


async def delete_document(
    session: AsyncSession, shipment_id: uuid.UUID, doc_id: uuid.UUID
) -> None:
    row = await get_document(session, shipment_id, doc_id)
    await session.delete(row)


async def extract_document_fields(
    session: AsyncSession,
    shipment_id: uuid.UUID,
    doc_id: uuid.UUID,
    *,
    kind_override: str | None = None,
) -> ShipmentDocumentRow:
    """Прогнать SGR-извлечение и сохранить результат в extracted.

    kind_override — человек выбрал тип вручную (например, после needs_kind).
    Иначе используется kind строки; placeholder 'other' запускает типизацию.
    """
    row = await get_document(session, shipment_id, doc_id)
    extracted = dict(row.extracted or {})
    source = extracted.get("source") or {}
    text = source.get("text") or ""
    if not text.strip():
        raise DocumentSourceError("У документа нет сохранённого текста для извлечения")

    kind = kind_override or (row.kind if row.kind != _KIND_PLACEHOLDER else None)
    payload = await extractors.extract_document(text, kind=kind)

    # source сохраняем, результат извлечения замещаем целиком.
    payload["source"] = source
    row.extracted = payload

    # Колонку kind обновляем только когда тип реально определён.
    if payload["kind"] in extractors.KIND_LABELS:
        row.kind = payload["kind"]

    # Номер/дату не затираем, только заполняем пустые из извлечённого.
    doc = payload.get("doc") or {}
    if not row.number and doc.get("number"):
        row.number = str(doc["number"])[:100]
    if not row.doc_date and doc.get("doc_date"):
        try:
            row.doc_date = _dt.date.fromisoformat(doc["doc_date"])
        except ValueError:
            pass

    await session.flush()
    return row


def build_g44_entries(rows: list[ShipmentDocumentRow]) -> list[DocEntry]:
    """Документы досье → записи графы 44 (код вида + номер + дата + TULPAR-статус)."""
    entries: list[DocEntry] = []
    for row in rows:
        permit_check = (row.extracted or {}).get("permit_check") or {}
        entries.append(DocEntry(
            kind=row.kind,
            code=DOC_KIND_CODES.get(row.kind),
            number=row.number,
            date=row.doc_date.isoformat() if row.doc_date else None,
            permit_status=permit_check.get("status"),
        ))
    return entries


def extracted_payloads(rows: list[ShipmentDocumentRow]) -> list[dict]:
    """extracted-payload'ы документов с успешным извлечением (для кросс-сверки)."""
    out: list[dict] = []
    for row in rows:
        data = row.extracted or {}
        if data.get("status") == "extracted" and data.get("doc"):
            out.append({**data, "doc_id": str(row.id), "row_kind": row.kind})
    return out


def document_out(row: ShipmentDocumentRow) -> DocumentOut:
    data = row.extracted or {}
    source = data.get("source") or {}
    return DocumentOut(
        id=row.id,
        shipment_id=row.shipment_id,
        kind=row.kind,
        number=row.number,
        doc_date=row.doc_date,
        source_origin=source.get("origin"),
        ocr_job_id=row.ocr_job_id,
        text_chars=len(source.get("text") or ""),
        status=data.get("status", "attached"),
        doc_type=data.get("doc_type"),
        doc=data.get("doc"),
        checks=list(data.get("checks") or []),
        sensor_warnings=list(data.get("sensor_warnings") or []),
        extracted_at=data.get("extracted_at"),
        created_at=row.created_at,
    )
