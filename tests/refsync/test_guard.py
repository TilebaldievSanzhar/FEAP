"""Гарантии справочников: сверка с ЕЭК (≤7 дней) и подпись эксперта."""

from __future__ import annotations

import datetime as dt

from feap.clock import BISHKEK_TZ, bishkek_date
from feap.db.models import RefDataset
from feap.refsync import guard
from feap.refsync.status import DATASETS

D = dt.date
REMEDIES = DATASETS["eaeu_remedies"]
ETT = DATASETS["ett_rates"]
VAT = DATASETS["vat_benefits"]


def _state(key: str, **kw) -> RefDataset:
    return RefDataset(dataset=key, status="ok", consecutive_failures=0, meta={}, **kw)


def test_bishkek_date_crosses_midnight():
    # 19:30 UTC 15.10 — уже 01:30 16.10 по Бишкеку.
    assert bishkek_date(dt.datetime(2026, 10, 15, 19, 30, tzinfo=dt.timezone.utc)) == D(2026, 10, 16)
    assert bishkek_date(dt.datetime(2026, 10, 15, 17, 59)) == D(2026, 10, 15)  # naive = UTC
    assert BISHKEK_TZ.utcoffset(None) == dt.timedelta(hours=6)


def test_eec_never_synced_blocks():
    check = guard.eec_check(REMEDIES, None, D(2026, 10, 16), horizon_days=7)
    assert check.blocking and "ещё не сверялись" in check.blocking
    assert not check.ok


def test_eec_within_horizon():
    synced = dt.datetime(2026, 10, 16, 1, 0, tzinfo=dt.timezone.utc)  # 07:00 по Бишкеку
    check = guard.eec_check(REMEDIES, _state("eaeu_remedies", last_success_at=synced),
                            D(2026, 10, 23), horizon_days=7)
    assert check.ok and check.guaranteed_until == D(2026, 10, 23)


def test_eec_beyond_horizon_blocks_with_dates():
    synced = dt.datetime(2026, 10, 1, 1, 0, tzinfo=dt.timezone.utc)
    check = guard.eec_check(REMEDIES, _state("eaeu_remedies", last_success_at=synced),
                            D(2026, 10, 16), horizon_days=7)
    assert check.blocking
    assert "01.10.2026" in check.blocking and "08.10.2026" in check.blocking
    assert check.to_json()["ok"] is False


def test_attestation_missing_and_stale_warn():
    today = D(2026, 10, 16)
    none = guard.attestation_check(VAT, None, today=today, max_age_days=45)
    assert none.warning and "не подтверждено" in none.warning and none.blocking is None

    old = guard.attestation_check(
        VAT, _state("vat_benefits", attested_at=dt.datetime(2026, 2, 17, tzinfo=dt.timezone.utc),
                    attested_by="эксперт"),
        today=today, max_age_days=45,
    )
    assert old.warning == "Льготы по НДС на импорт — сверено 17.02.2026 (эксперт)"

    fresh = guard.attestation_check(
        VAT, _state("vat_benefits", attested_at=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)),
        today=today, max_age_days=45,
    )
    assert fresh.ok


def test_evaluate_and_single_aggregated_warning():
    states = {
        "eaeu_remedies": _state("eaeu_remedies",
                                last_success_at=dt.datetime(2026, 10, 16, 1, tzinfo=dt.timezone.utc)),
        "ett_rates": _state("ett_rates",
                            last_success_at=dt.datetime(2026, 10, 16, 0, 50, tzinfo=dt.timezone.utc)),
        "vat_benefits": _state("vat_benefits",
                               attested_at=dt.datetime(2026, 2, 17, tzinfo=dt.timezone.utc)),
    }
    checks = guard.evaluate(states, D(2026, 10, 16), today=D(2026, 10, 16),
                            horizon_days=7, max_age_days=45)
    assert [c.dataset for c in checks] == list(guard.CALC_DATASETS)
    assert not any(c.blocking for c in checks)
    warning = guard.attestation_warning(checks)
    assert warning.startswith("Справочники без свежей сверки экспертом: ")
    assert "Льготы по НДС на импорт — сверено 17.02.2026" in warning
    assert "Ставки акциза — не подтверждено экспертом" in warning
    assert "ЕТТ" not in warning  # ЕТТ сверяется с ЕЭК ежедневно, не подписью
    ett_check = next(c for c in checks if c.dataset == "ett_rates")
    assert ett_check.guarantee == "eec" and ett_check.guaranteed_until == D(2026, 10, 19)
    assert guard.attestation_warning([c for c in checks if c.ok]) is None
