"""Findings-fixer: whitelist путей, применение патча, верификация повторным ФЛК."""

from __future__ import annotations

from decimal import Decimal

import pytest

import feap.dt.service as dt_service
from feap.dt import fixer
from feap.dt.fixer import (
    FixerResult,
    FixSuggestion,
    apply_patch,
    parse_field_path,
    suggest_fixes,
    verify_suggestion,
)
from feap.dt.schemas import AssembledDraft, Finding, PartyIn, ShipmentIn, ShipmentItemIn


def _model() -> ShipmentIn:
    return ShipmentIn(
        seller=PartyIn(name="Alpha"), buyer=PartyIn(name="Beta"),
        incoterms="CIF", contract_currency="USD",
        items=[
            ShipmentItemIn(description="Товар один", price=Decimal("100")),
            ShipmentItemIn(description="Товар два", price=Decimal("200")),
        ],
    )


class TestParseFieldPath:
    def test_header_field(self):
        assert parse_field_path("freight_to_border", 2) == ("header", None, "freight_to_border")

    def test_item_field(self):
        assert parse_field_path("items[1].net_kg", 2) == ("item", 1, "net_kg")

    @pytest.mark.parametrize("path,fragment", [
        ("contract_currency", "whitelist"),        # валюту менять нельзя
        ("items[5].net_kg", "не существует"),
        ("items[0].shipment_id", "не редактируется"),
        ("data['x']", "whitelist"),
        ("items[0]", "whitelist"),
    ])
    def test_rejects(self, path, fragment):
        with pytest.raises(ValueError, match=fragment):
            parse_field_path(path, 2)


class TestApplyPatch:
    def test_decimal_coercion(self):
        patched = apply_patch(_model(), "items[0].net_kg", "12.5")
        assert patched.items[0].net_kg == Decimal("12.5")
        # исходная модель не тронута
        assert _model().items[0].net_kg is None

    def test_header_value(self):
        patched = apply_patch(_model(), "freight_to_border", "150")
        assert patched.freight_to_border == Decimal("150")

    def test_empty_clears_field(self):
        model = _model()
        model.items[0].net_kg = Decimal("5")
        patched = apply_patch(model, "items[0].net_kg", None)
        assert patched.items[0].net_kg is None

    def test_garbage_value_raises(self):
        with pytest.raises(ValueError, match="items\\[0\\].net_kg"):
            apply_patch(_model(), "items[0].net_kg", "двенадцать")

    def test_negative_price_rejected_by_schema(self):
        with pytest.raises(ValueError):
            apply_patch(_model(), "items[0].price", "-5")


def _finding(rule: str = "F-04", graph: str | None = "g22",
             severity: str = "error") -> Finding:
    return Finding(rule=rule, severity=severity, graph=graph, message="тест")


def _propose(**overrides) -> FixSuggestion:
    base = dict(
        finding_rule="F-04", finding_graph="g22", action="propose_value",
        field_path="freight_to_border", proposed_value="150",
        why="из инвойса", evidence=["invoice INV-1"],
    )
    base.update(overrides)
    return FixSuggestion(**base)


def _fake_validate(findings_after: list[Finding], missing: list[str] | None = None):
    async def fake(session, model, *, shipment_ref=None, doc_payloads=None,
                   documents_g44=None):
        return (
            AssembledDraft(graphs={}, missing_parameters=missing or []),
            findings_after,
        )
    return fake


class TestVerifySuggestion:
    async def test_verified_when_finding_gone(self, monkeypatch):
        monkeypatch.setattr(dt_service, "assemble_and_validate", _fake_validate([]))
        v = await verify_suggestion(
            None, _model(), _propose(),
            shipment_ref=None, doc_payloads=[], baseline_findings=[_finding()],
        )
        assert v["applied"] and v["verified"] is True
        assert v["target_gone"] is True
        assert v["status_after"] == "ready"

    async def test_not_verified_when_finding_stays(self, monkeypatch):
        monkeypatch.setattr(
            dt_service, "assemble_and_validate", _fake_validate([_finding()])
        )
        v = await verify_suggestion(
            None, _model(), _propose(),
            shipment_ref=None, doc_payloads=[], baseline_findings=[_finding()],
        )
        assert v["verified"] is False
        assert "не исчез" in v["note"]

    async def test_not_verified_when_new_errors_appear(self, monkeypatch):
        after = [_finding(rule="F-05", graph="g12"), _finding(rule="F-08", graph="g23")]
        monkeypatch.setattr(dt_service, "assemble_and_validate", _fake_validate(after))
        v = await verify_suggestion(
            None, _model(), _propose(),
            shipment_ref=None, doc_payloads=[], baseline_findings=[_finding()],
        )
        assert v["target_gone"] is True
        assert v["verified"] is False
        assert "новые ошибки" in v["note"]

    async def test_rule_without_graph_requires_all_gone(self, monkeypatch):
        after = [_finding(rule="F-01", graph="g08")]
        monkeypatch.setattr(dt_service, "assemble_and_validate", _fake_validate(after))
        v = await verify_suggestion(
            None, _model(), _propose(finding_rule="F-01", finding_graph=None),
            shipment_ref=None, doc_payloads=[],
            baseline_findings=[_finding(rule="F-01", graph="g08"),
                               _finding(rule="F-01", graph="g14")],
        )
        assert v["target_gone"] is False

    async def test_bad_path_reported_not_raised(self):
        v = await verify_suggestion(
            None, _model(), _propose(field_path="__dict__"),
            shipment_ref=None, doc_payloads=[], baseline_findings=[_finding()],
        )
        assert v["applied"] is False and v["verified"] is False
        assert "whitelist" in v["note"]

    async def test_ask_user_not_applied(self):
        suggestion = FixSuggestion(
            finding_rule="F-04", action="ask_user",
            question="Какой фрахт?", why="нет данных",
        )
        v = await verify_suggestion(
            None, _model(), suggestion,
            shipment_ref=None, doc_payloads=[], baseline_findings=[_finding()],
        )
        assert v["applied"] is False and v["verified"] is None


class TestSuggestFixes:
    async def test_no_actionable_findings_no_llm(self):
        out = await suggest_fixes(None, _model(), [_finding(severity="info")])
        assert out.llm_used is False
        assert out.suggestions == []

    async def test_full_flow(self, monkeypatch):
        async def fake_llm(*, level, **kw):
            assert level == 95
            return FixerResult(
                analysis="фрахт нулевой при CIF",
                suggestions=[
                    _propose(),
                    FixSuggestion(finding_rule="F-99", action="cannot_fix",
                                  why="такого правила нет"),
                ],
            )

        monkeypatch.setattr(fixer, "llm_classify", fake_llm)
        monkeypatch.setattr(dt_service, "assemble_and_validate", _fake_validate([]))

        out = await suggest_fixes(
            None, _model(), [_finding()], shipment_ref="s1", doc_payloads=[],
        )

        assert out.llm_used is True
        assert out.analysis.startswith("фрахт")
        first, second = out.suggestions
        assert first["verification"]["verified"] is True
        assert second["verification"]["applied"] is False
        assert "F-99" in second["verification"]["note"]
