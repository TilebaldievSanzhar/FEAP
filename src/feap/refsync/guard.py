"""Гарантии справочников на дату расчёта (кроме курсов — они в refsync.fx).

- eec: решения ЕЭК вступают в силу не ранее 10 дней после опубликования,
  поэтому сверка с ЕЭК в день S гарантирует данные на дни ≤ S + 7 (запас 3 дня).
  Дальше гарантии нет → блокирующая проблема (needs_input в режиме enforce).
- attestation: у актов КР ленты изменений нет, акт может действовать со дня
  опубликования — гарантию даёт только подпись эксперта «сверено на дату».
  Старше REFDATA_ATTESTATION_MAX_AGE_DAYS или не подписано → предупреждение.

Функции *_check — чистые (тестируются без БД); check_datasets читает состояние.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from feap.clock import bishkek_date, today_bishkek
from feap.config import settings
from feap.db.models import RefDataset
from feap.refsync.status import DATASETS, DatasetSpec, load_datasets

#: Справочники, которые проверяются при каждом расчёте платежей.
CALC_DATASETS: tuple[str, ...] = (
    "eaeu_remedies", "ett_rates", "vat_benefits", "excise_rates", "customs_fee", "trade_agreements",
)


@dataclass(frozen=True)
class DatasetCheck:
    dataset: str
    title: str
    guarantee: str
    checked_at: dt.datetime | None
    checked_by: str | None = None
    guaranteed_until: dt.date | None = None
    blocking: str | None = None
    warning: str | None = None

    @property
    def ok(self) -> bool:
        return self.blocking is None and self.warning is None

    def to_json(self) -> dict:
        return {
            "dataset": self.dataset,
            "title": self.title,
            "guarantee": self.guarantee,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "checked_by": self.checked_by,
            "guaranteed_until": self.guaranteed_until.isoformat() if self.guaranteed_until else None,
            "ok": self.ok,
            "message": self.blocking or self.warning,
        }


def eec_check(
    spec: DatasetSpec, state: RefDataset | None, day: dt.date, *, horizon_days: int,
) -> DatasetCheck:
    last = state.last_success_at if state is not None else None
    if last is None:
        return DatasetCheck(
            spec.key, spec.title, spec.guarantee, None,
            blocking=f"{spec.title}: ещё не сверялись с ЕЭК — данные на "
                     f"{day:%d.%m.%Y} не подтверждены",
        )
    until = bishkek_date(last) + dt.timedelta(days=horizon_days)
    blocking = None
    if day > until:
        blocking = (f"{spec.title}: последняя сверка с ЕЭК {bishkek_date(last):%d.%m.%Y} — на "
                    f"{day:%d.%m.%Y} нельзя гарантировать, что нет новых решений "
                    f"(гарантия до {until:%d.%m.%Y})")
    return DatasetCheck(spec.key, spec.title, spec.guarantee, last,
                        guaranteed_until=until, blocking=blocking)


def attestation_check(
    spec: DatasetSpec, state: RefDataset | None, *, today: dt.date, max_age_days: int,
) -> DatasetCheck:
    attested = state.attested_at if state is not None else None
    by = state.attested_by if state is not None else None
    if attested is None:
        return DatasetCheck(spec.key, spec.title, spec.guarantee, None,
                            warning=f"{spec.title} — не подтверждено экспертом")
    on = bishkek_date(attested)
    warning = None
    if (today - on).days > max_age_days:
        warning = f"{spec.title} — сверено {on:%d.%m.%Y}" + (f" ({by})" if by else "")
    return DatasetCheck(spec.key, spec.title, spec.guarantee, attested, checked_by=by,
                        warning=warning)


def evaluate(
    states: dict[str, RefDataset],
    day: dt.date,
    *,
    today: dt.date,
    keys: tuple[str, ...] = CALC_DATASETS,
    horizon_days: int,
    max_age_days: int,
) -> list[DatasetCheck]:
    checks: list[DatasetCheck] = []
    for key in keys:
        spec = DATASETS[key]
        state = states.get(key)
        if spec.guarantee == "eec":
            horizon = (getattr(settings, spec.horizon_setting) if spec.horizon_setting
                       else horizon_days)
            checks.append(eec_check(spec, state, day, horizon_days=horizon))
        elif spec.guarantee == "attestation":
            checks.append(attestation_check(spec, state, today=today, max_age_days=max_age_days))
    return checks


def attestation_warning(checks: list[DatasetCheck]) -> str | None:
    """Одно предупреждение на расчёт вместо пяти — W-REF-ATTEST."""
    stale = [c.warning for c in checks if c.warning]
    if not stale:
        return None
    return ("Справочники без свежей сверки экспертом: " + "; ".join(stale)
            + ". Изменения в них могли быть не учтены — проверьте ставки перед подачей")


async def check_datasets(
    session: AsyncSession, day: dt.date, *, keys: tuple[str, ...] = CALC_DATASETS,
) -> list[DatasetCheck]:
    return evaluate(
        await load_datasets(session), day, today=today_bishkek(), keys=keys,
        horizon_days=settings.REFDATA_EEC_GUARANTEE_DAYS,
        max_age_days=settings.REFDATA_ATTESTATION_MAX_AGE_DAYS,
    )
