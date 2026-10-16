"""Курс не подтверждён → needs_input с причиной; след курса и справочников в meta; ФЛК W-RATE-*/W-REF-*."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from feap.calc.engine import refdata_out
from feap.dt.assembler import ItemRef, Rates, assemble
from feap.dt.flk import CodeInfo, draft_status, validate_draft
from feap.dt.refdata import RefSnapshot
from feap.dt.schemas import PartyIn, ShipmentIn, ShipmentItemIn
from feap.refsync.guard import DatasetCheck

DAY = dt.date(2026, 10, 17)
REF = ItemRef(code_exists=True, is_leaf=True, duty_raw="10%", vat_rate="12%")
USD_QUOTE = {"currency": "USD", "day": "2026-10-17", "rate": "87.45", "source": "nbkr_daily",
             "effective_from": "2026-10-17", "fetched_at": "2026-10-16T10:05:00+00:00",
             "published_at": None}


class Ctx:
    def code_info(self, code10: str) -> CodeInfo | None:
        return CodeInfo(exists=True, is_leaf=True)

    def classifier_codes(self, classifier: str) -> set[str] | None:
        return None

    def has_ntm_measures(self, code10: str) -> bool:
        return False


def shipment(**over) -> ShipmentIn:
    base = dict(
        seller=PartyIn(name="Seller", country="CN"),
        buyer=PartyIn(name="Buyer", country="KG"),
        incoterms="FOB", contract_currency="USD",
        items=[ShipmentItemIn(description="Кастрюли", hs_code="7615101000",
                              origin_country="TR", price=Decimal("1000"), net_kg=Decimal("100"),
                              gross_kg=Decimal("110"))],
    )
    base.update(over)
    return ShipmentIn(**base)


def test_unconfirmed_rate_gives_reason_in_needs_input():
    reason = "курс на 17.10.2026 ещё не опубликован — НБКР публикует курс на следующий день около 16:00 по Бишкеку"
    rates = Rates(rate_date=DAY, contract=None, usd=None, eur=None,
                  issues={"USD": reason, "EUR": reason})
    assembled = assemble(shipment(), [REF], rates)
    missing = assembled.missing_parameters
    assert f"курс USD НБКР на 2026-10-17 (графа 23): {reason}" in missing
    # Графа 46 без курса графы 23 не считается — второй раз о курсе не пишем.
    assert not any("(графа 46)" in m for m in missing)
    assert draft_status([], missing) == "needs_input"
    graphs = assembled.graphs
    assert graphs["header"]["g23"] == {"rate": None, "date": "2026-10-17"}
    # Без курса — ни одной суммы, никаких молчаливых нулей.
    assert graphs["header"]["g12"] is None and graphs["totals"]["g12"] is None
    assert graphs["totals"]["payments"] == {} and graphs["totals"]["fee_line"] is None
    item = graphs["items"][0]
    assert item["g45"] is None and item["g46"] is None
    assert item["g47"] and all(line["amount"] is None for line in item["g47"])


def test_specific_duty_currency_reason_is_appended():
    eur_reason = "нет связи с НБКР — курс не подтверждён"
    ref = ItemRef(code_exists=True, is_leaf=True, duty_raw="1.75 EUR за 1 кг", vat_rate="12%")
    rates = Rates(rate_date=DAY, contract=Decimal("87.45"), usd=Decimal("87.45"), eur=None,
                  issues={"EUR": eur_reason})
    missing = assemble(shipment(), [ref], rates).missing_parameters
    assert any(m.endswith(f"курс EUR на дату расчёта: {eur_reason}") for m in missing), missing


def test_meta_carries_fx_and_refdata_trace():
    rates = Rates(rate_date=DAY, contract=Decimal("87.45"), usd=Decimal("87.45"),
                  eur=Decimal("98.08"), provenance={"USD": USD_QUOTE})
    refdata = {"datasets": [{"dataset": "eaeu_remedies", "title": "Меры", "guarantee": "eec",
                             "checked_at": None, "checked_by": None, "guaranteed_until": "2026-10-23",
                             "ok": True, "message": None}]}
    graphs = assemble(shipment(), [REF], rates, refdata=refdata).graphs
    assert graphs["meta"]["fx"] == {"USD": USD_QUOTE}
    assert graphs["meta"]["refdata"] == refdata

    out = refdata_out(graphs, currencies=["USD"], cost_fx=[
        {"currency": "CNY", "day": "2026-10-17", "rate": "13.07", "source": "nbkr_daily"},
        {"currency": "USD", "day": "2026-10-17", "rate": "87.45", "source": "nbkr_daily"},
    ])
    assert [f.currency for f in out.fx] == ["USD", "CNY"]
    assert out.fx[0].rate == Decimal("87.45") and out.fx[0].source == "nbkr_daily"
    assert out.datasets[0].guaranteed_until == dt.date(2026, 10, 23)


def _findings(meta_fx: dict, refdata: dict) -> list:
    rates = Rates(rate_date=DAY, contract=Decimal("87.50"), usd=Decimal("87.50"),
                  eur=Decimal("98"), provenance=meta_fx)
    model = shipment()
    graphs = assemble(model, [REF], rates, refdata=refdata).graphs
    return validate_draft(graphs, model, Ctx())


def test_manual_rate_warning():
    manual = {"currency": "USD", "day": "2026-10-17", "rate": "87.50", "source": "manual",
              "issue": "нет связи с НБКР — курс не подтверждён"}
    findings = _findings({"USD": manual}, {})
    w = [f for f in findings if f.rule == "W-RATE-MANUAL"]
    assert len(w) == 1 and w[0].severity == "warning" and w[0].graph == "g23"
    assert "введён вручную (87.50)" in w[0].message and "нет связи с НБКР" in w[0].message


def test_manual_rate_ignored_and_attestation_and_guard_warn_mode():
    refdata = {
        "manual_fx_ignored": [{"currency": "USD", "manual": "88", "official": "87.45",
                               "day": "2026-10-17"}],
        "attestation_warning": "Справочники без свежей сверки экспертом: Ставки ЕТТ — сверено 17.02.2026",
        "blocking": ["Меры защиты рынка ЕАЭС: последняя сверка с ЕЭК 01.10.2026"],
        "guard_mode": "warn",
    }
    by_rule = {f.rule: f for f in _findings({"USD": USD_QUOTE}, refdata)}
    assert by_rule["W-RATE-MANUAL-IGNORED"].severity == "info"
    assert "применён официальный" in by_rule["W-RATE-MANUAL-IGNORED"].message
    assert by_rule["W-REF-ATTEST"].message.startswith("Справочники без свежей сверки")
    assert "Режим разработки" in by_rule["W-REF-GUARD"].message
    assert "W-RATE-MANUAL" not in by_rule


def test_guard_enforce_mode_has_no_guard_finding():
    findings = _findings({}, {"blocking": ["x"], "guard_mode": "enforce"})
    assert not any(f.rule == "W-REF-GUARD" for f in findings)


def test_snapshot_refdata_meta():
    snap = RefSnapshot(checks=[
        DatasetCheck("eaeu_remedies", "Меры", "eec", None, blocking="Меры: ещё не сверялись"),
        DatasetCheck("ett_rates", "Ставки ЕТТ", "attestation", None, warning="Ставки ЕТТ — не подтверждено экспертом"),
    ])
    meta = snap.refdata_meta()
    assert meta["blocking"] == ["Меры: ещё не сверялись"]
    assert "Ставки ЕТТ — не подтверждено экспертом" in meta["attestation_warning"]
    assert [d["ok"] for d in meta["datasets"]] == [False, False]


@pytest.mark.parametrize("rates,error", [
    ({"usd": "87.5"}, None),
    ({"KGS": "1"}, "кроме KGS"),
    ({"US": "87"}, "ISO 4217"),
    ({"USD": "0"}, "положительное"),
    ({"USD": "-5"}, "положительное"),
])
def test_manual_fx_validation(rates, error):
    if error is None:
        assert shipment(manual_fx_rates=rates).manual_fx_rates == {"USD": Decimal("87.5")}
    else:
        with pytest.raises(ValidationError, match=error):
            shipment(manual_fx_rates=rates)
