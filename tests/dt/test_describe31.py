"""Графа 31: факты, числовой grounding, retry-цикл отклонений."""

from __future__ import annotations

from decimal import Decimal

import pytest

from feap.dt import describe31
from feap.dt.describe31 import (
    Fact,
    G31Result,
    build_facts,
    generate_g31,
    number_tokens,
    validate_g31,
)
from feap.dt.schemas import ShipmentItemIn


def _item(**overrides) -> ShipmentItemIn:
    base = dict(
        description="Зубная щётка электрическая, звуковая",
        price=Decimal("500"),
        qty=Decimal("100"), qty_unit="шт",
        net_kg=Decimal("25.5"),
        sku="TB-01",
        hs_code="8509800000",
        extra={"classification": {
            "final_code": "8509800000",
            "code_description": "Машины электромеханические бытовые",
            "confidence": 0.9,
        }},
    )
    base.update(overrides)
    return ShipmentItemIn(**base)


class TestBuildFacts:
    def test_card_facts(self):
        facts = {f.id: f for f in build_facts(_item())}
        assert facts["item.description"].value.startswith("Зубная щётка")
        assert facts["item.qty"].value == "100 шт"
        assert facts["item.net_kg"].value == "25.5"
        assert "8509800000" in facts["classification.code"].value
        assert "8509 80 000 0" in facts["classification.code"].value
        assert facts["classification.code_description"].source == "классификация"

    def test_catalog_and_doc_facts_by_sku(self):
        docs = [{
            "kind": "invoice",
            "doc": {"number": "INV-1", "lines": [
                {"line_no": 1, "sku": "tb-01", "description": "Sonic toothbrush X7",
                 "qty": "100"},
                {"line_no": 2, "sku": "OTHER", "description": "чужая строка"},
            ]},
        }]
        facts = {f.id: f for f in build_facts(
            _item(), catalog_attributes={"материал": "пластик ABS"}, docs=docs,
        )}
        assert facts["catalog.материал"].value == "пластик ABS"
        assert facts["doc0.line1.description"].value == "Sonic toothbrush X7"
        assert "doc0.line2.description" not in facts

    def test_no_optional_data(self):
        facts = build_facts(ShipmentItemIn(description="Товар", price=Decimal("1")))
        assert {f.id for f in facts} == {"item.description"}


class TestNumberGrounding:
    @pytest.mark.parametrize("text,expected", [
        ("мощность 80 Вт", {"80"}),
        ("вес 25,50 кг", {"25.5"}),
        ("код 8509 80 000 0", {"8509", "80", "0"}),
        ("2 шт", set()),          # одна цифра — не считаем
        ("без чисел", set()),
    ])
    def test_tokens(self, text, expected):
        assert number_tokens(text) == expected

    def test_validator_accepts_grounded(self):
        facts = build_facts(_item())
        result = G31Result(
            facts_review="ок", missing_attributes=[],
            text_ru="Щётки зубные электрические звуковые, артикул TB-01, 100 шт, "
                    "вес нетто 25.5 кг",
            facts_used=["item.description", "item.qty", "item.net_kg", "item.sku"],
        )
        assert validate_g31(result, facts) == []

    def test_validator_rejects_foreign_numbers(self):
        facts = build_facts(_item())
        result = G31Result(
            facts_review="ок", missing_attributes=[],
            text_ru="Щётка электрическая мощностью 35 Вт, 100 шт",
            facts_used=["item.description", "item.qty"],
        )
        reasons = validate_g31(result, facts)
        assert any("35" in r for r in reasons)

    def test_validator_rejects_unknown_fact_ids(self):
        facts = build_facts(_item())
        result = G31Result(
            facts_review="ок", missing_attributes=[],
            text_ru="Щётка зубная электрическая, 100 шт в упаковке",
            facts_used=["item.description", "made.up.fact"],
        )
        reasons = validate_g31(result, facts)
        assert any("made.up.fact" in r for r in reasons)

    def test_validator_rejects_empty_facts_used(self):
        facts = build_facts(_item())
        result = G31Result(
            facts_review="ок", missing_attributes=[],
            text_ru="Достаточно длинный текст без ссылок на факты",
            facts_used=[],
        )
        assert any("facts_used" in r for r in validate_g31(result, facts))

    def test_validator_rejects_short_text(self):
        facts = build_facts(_item())
        result = G31Result(
            facts_review="ок", missing_attributes=[],
            text_ru="Щётка", facts_used=["item.description"],
        )
        assert any("короче" in r for r in validate_g31(result, facts))


class TestGenerateRetry:
    async def test_retry_then_accept(self, monkeypatch):
        bad = G31Result(
            facts_review="…", missing_attributes=[],
            text_ru="Щётка электрическая мощностью 9999 Вт совершенно точно",
            facts_used=["item.description"],
        )
        good = G31Result(
            facts_review="…", missing_attributes=["материал корпуса"],
            text_ru="Щётки зубные электрические звуковые, артикул TB-01, 100 шт",
            facts_used=["item.description", "item.sku", "item.qty"],
        )
        responses = iter([bad, good])
        feedbacks: list[str] = []

        async def fake_llm(*, level, system_prompt, user_message, output_model, **kw):
            assert level == 93
            feedbacks.append(user_message)
            return next(responses)

        monkeypatch.setattr(describe31, "llm_classify", fake_llm)
        out = await generate_g31(_item())

        assert out.accepted is True
        assert out.attempts == 2
        assert out.missing_attributes == ["материал корпуса"]
        # вторая попытка получила фидбек об отклонении
        assert "ОТКЛОНЕНА" in feedbacks[1]
        assert "9999" in feedbacks[1]

    async def test_rejection_after_limit(self, monkeypatch):
        bad = G31Result(
            facts_review="…", missing_attributes=[],
            text_ru="Изделие из титана 777 массой 55 кг",
            facts_used=["item.description"],
        )

        calls = 0

        async def fake_llm(**kw):
            nonlocal calls
            calls += 1
            return bad

        monkeypatch.setattr(describe31, "llm_classify", fake_llm)
        out = await generate_g31(_item())

        assert out.accepted is False
        assert calls == 3          # 1 + DT_G31_MAX_REJECTIONS(2)
        assert out.attempts == 3
        assert out.rejection_reasons
        assert out.text_ru          # текст отдаём, но с флагом accepted=False


def test_fact_dataclass_roundtrip():
    fact = Fact(id="a", label="b", value="c", source="карточка")
    assert fact.id == "a" and fact.source == "карточка"
