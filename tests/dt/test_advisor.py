"""Риск-советник: детерминированные сигналы + harness-фильтрация LLM-ответа."""

from __future__ import annotations

from decimal import Decimal

from feap.dt import advisor
from feap.dt.advisor import (
    AdvisorResult,
    CodeCheck,
    RiskAdvice,
    advise,
    compute_signals,
)
from feap.dt.schemas import PartyIn, ShipmentIn, ShipmentItemIn


def _model(items: list[ShipmentItemIn]) -> ShipmentIn:
    return ShipmentIn(
        seller=PartyIn(name="Alpha"), buyer=PartyIn(name="Beta"),
        incoterms="CIF", contract_currency="USD", items=items,
    )


def _item(price: str, net: str | None, **kw) -> ShipmentItemIn:
    return ShipmentItemIn(
        description="Товар для теста сигналов",
        price=Decimal(price),
        net_kg=Decimal(net) if net is not None else None,
        **kw,
    )


class TestComputeSignals:
    def test_price_per_kg_outlier(self):
        # медиана ~10/кг; выброс 100/кг (×10 > фактора 4)
        model = _model([
            _item("100", "10"), _item("110", "11"), _item("90", "9"),
            _item("1000", "10"),
        ])
        ids = [s.id for s in compute_signals(model)]
        assert "S-PRICEKG-4" in ids
        assert not any(i.startswith("S-PRICEKG-1") for i in ids)

    def test_outlier_needs_three_items(self):
        model = _model([_item("100", "10"), _item("1000", "10")])
        assert not any(
            s.kind == "price_per_kg_outlier" for s in compute_signals(model)
        )

    def test_missing_net_weight(self):
        model = _model([_item("100", None)])
        signals = compute_signals(model)
        assert [s.id for s in signals] == ["S-NONET-1"]
        assert signals[0].severity == "warning"

    def test_net_equals_gross(self):
        model = _model([_item("100", "10", gross_kg=Decimal("10"))])
        kinds = {s.kind for s in compute_signals(model)}
        assert "net_equals_gross" in kinds

    def test_low_confidence(self):
        model = _model([_item(
            "100", "10",
            extra={"classification": {"confidence": 0.4}},
        )])
        assert any(s.id == "S-LOWCONF-1" for s in compute_signals(model))
        model_ok = _model([_item(
            "100", "10", extra={"classification": {"confidence": 0.9}},
        )])
        assert not any(s.kind == "low_classification_confidence"
                       for s in compute_signals(model_ok))

    def test_clean_dossier_no_signals(self):
        model = _model([_item("100", "10", gross_kg=Decimal("12"))])
        assert compute_signals(model) == []


class TestAdviseHarness:
    async def test_no_signals_no_codes_no_llm(self):
        model = _model([_item("100", "10", gross_kg=Decimal("12"))])
        out = await advise(model, {})
        assert out.llm_used is False
        assert out.signals == []

    async def test_filters_unknown_ids_and_positions(self, monkeypatch):
        model = _model([_item("100", None, hs_code="8509800000")])
        code_names = {1: ("8509800000", "Машины электромеханические бытовые")}

        async def fake_llm(*, level, **kw):
            assert level == 94
            return AdvisorResult(
                risk_review="…",
                advices=[
                    RiskAdvice(signal_id="S-NONET-1", explanation="нет веса",
                               recommendation="указать вес"),
                    RiskAdvice(signal_id="S-FAKE-9", explanation="выдумка",
                               recommendation="—"),
                ],
                code_checks=[
                    CodeCheck(position=1, verdict="consistent", reasoning="ок"),
                    CodeCheck(position=7, verdict="mismatch", reasoning="нет позиции"),
                ],
            )

        monkeypatch.setattr(advisor, "llm_classify", fake_llm)
        out = await advise(model, code_names)

        assert out.llm_used is True
        assert [a["signal_id"] for a in out.advices] == ["S-NONET-1"]
        assert [c["position"] for c in out.code_checks] == [1]
        assert len(out.dropped) == 2

    async def test_llm_called_for_code_checks_without_signals(self, monkeypatch):
        model = _model([_item("100", "10", gross_kg=Decimal("12"),
                              hs_code="8509800000")])
        code_names = {1: ("8509800000", "Машины электромеханические")}

        async def fake_llm(**kw):
            return AdvisorResult(risk_review="…", advices=[], code_checks=[])

        monkeypatch.setattr(advisor, "llm_classify", fake_llm)
        out = await advise(model, code_names)
        assert out.llm_used is True
        assert out.signals == []
