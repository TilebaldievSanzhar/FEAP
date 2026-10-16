"""Статус справочников: источник, дата сверки, гарантия (открыто, без входа).

GET /api/v1/reference/data-status — «Источник и дата сверки» для интерфейса:
когда сверялись курсы НБКР и меры ЕЭК, до какой даты гарантированы данные,
кто и когда подтвердил ручные справочники.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from feap.api.deps import get_db
from feap.clock import bishkek_date, today_bishkek
from feap.config import settings
from feap.db.models import RefChange
from feap.refsync.guard import attestation_check, eec_check
from feap.refsync.schemas import DataStatusEntry, DataStatusOut
from feap.refsync.status import DATASETS, load_datasets

router = APIRouter(prefix="/api/v1", tags=["reference"])


def _ru(iso: str) -> str:
    return dt.date.fromisoformat(iso).strftime("%d.%m.%Y")


@router.get("/reference/data-status", response_model=DataStatusOut)
async def data_status(db: AsyncSession = Depends(get_db)) -> DataStatusOut:
    states = await load_datasets(db)
    today = today_bishkek()
    entries: list[DataStatusEntry] = []
    for key, spec in DATASETS.items():
        st = states.get(key)
        meta = (st.meta or {}) if st is not None else {}
        guaranteed_until: dt.date | None = None
        if spec.guarantee == "eec":
            horizon = (getattr(settings, spec.horizon_setting) if spec.horizon_setting
                       else settings.REFDATA_EEC_GUARANTEE_DAYS)
            check = eec_check(spec, st, today, horizon_days=horizon)
            guaranteed_until, ok = check.guaranteed_until, check.ok
        elif spec.guarantee == "attestation":
            ok = attestation_check(
                spec, st, today=today, max_age_days=settings.REFDATA_ATTESTATION_MAX_AGE_DAYS,
            ).ok
        elif spec.guarantee == "fx":
            ok = (st is not None and st.status == "ok" and st.last_success_at is not None
                  and bishkek_date(st.last_success_at) == today and not meta.get("warning"))
        else:
            ok = st is not None and st.status == "ok"
        publication = meta.get("publication_date")
        detail = None
        if key == "ett_rates" and meta.get("loaded_groups"):
            latest = meta.get("latest_version")
            notes_v = meta.get("notes_version")
            detail = (f"групп ЕТТ: {meta['loaded_groups']}"
                      + (f" · последняя редакция группы {_ru(latest)}" if latest else "")
                      + (f" · примечания к ЕТТ от {_ru(notes_v)}" if notes_v else ""))
        entries.append(DataStatusEntry(
            dataset=key, title=spec.title, source=spec.source, source_url=spec.source_url,
            cadence=spec.cadence, guarantee=spec.guarantee,
            status=st.status if st is not None else "unknown",
            last_attempt_at=st.last_attempt_at if st is not None else None,
            last_success_at=st.last_success_at if st is not None else None,
            last_change_at=st.last_change_at if st is not None else None,
            last_error=st.last_error if st is not None and st.status == "failing" else None,
            publication_date=dt.date.fromisoformat(publication) if publication else None,
            warning=meta.get("warning"),
            detail=detail,
            attested_at=st.attested_at if st is not None else None,
            attested_by=st.attested_by if st is not None else None,
            attested_note=st.attested_note if st is not None else None,
            guaranteed_until=guaranteed_until,
            ok=ok,
        ))
    pending = (await db.execute(
        select(func.count()).select_from(RefChange).where(RefChange.status == "pending")
    )).scalar_one()
    return DataStatusOut(today=today, datasets=entries, pending_changes=pending)
