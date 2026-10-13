"""Торговые режимы ЕАЭС по стране происхождения."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from feap.tariff.agreements import agreement_from_row, regime_for_origin

SEED = Path(__file__).resolve().parents[2] / "data" / "trade_agreements_seed.json"
AGREEMENTS = [
    agreement_from_row(r)
    for r in json.loads(SEED.read_text(encoding="utf-8"))["agreements"]
]


def test_vietnam_fta_in_force():
    regime = regime_for_origin(AGREEMENTS, "vn", dt.date(2026, 10, 13))
    assert regime is not None and regime.regime == "fta"


def test_uae_only_from_entry_into_force():
    assert regime_for_origin(AGREEMENTS, "AE", dt.date(2026, 10, 5)) is None
    assert regime_for_origin(AGREEMENTS, "AE", dt.date(2026, 10, 6)).regime == "fta"


def test_signed_but_not_in_force_is_ignored():
    assert regime_for_origin(AGREEMENTS, "ID", dt.date(2026, 10, 13)) is None


def test_interim_agreement():
    assert regime_for_origin(AGREEMENTS, "MN", dt.date(2026, 8, 1)).regime == "fta_interim"
    assert regime_for_origin(AGREEMENTS, "MN", dt.date(2026, 7, 1)) is None


def test_eaeu_member_and_general_regime():
    assert regime_for_origin(AGREEMENTS, "KZ", dt.date(2026, 10, 13)).regime == "eaeu_member"
    assert regime_for_origin(AGREEMENTS, "CN", dt.date(2026, 10, 13)) is None
    assert regime_for_origin(AGREEMENTS, None, dt.date(2026, 10, 13)) is None


def test_seed_rows_in_force_have_dates():
    for a in AGREEMENTS:
        if a.status == "in_force":
            assert a.in_force_from is not None, a.country
