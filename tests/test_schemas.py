"""Tests for Pydantic schemas."""

from feap.schemas.product_card import ProductCard
from feap.schemas.section import SectionCandidate, SectionResult
from feap.schemas.common import BacktrackTrigger, DecisionChainEntry
from feap.schemas.validation import ClassificationResult, ClassificationChain


def test_product_card_minimal():
    card = ProductCard(
        original_description="блендер",
        normalized_description="блендер бытовой электрический",
    )
    assert card.original_description == "блендер"
    assert card.is_set is False
    assert card.missing_info == []


def test_product_card_full():
    card = ProductCard(
        original_description="блендер Philips",
        normalized_description="блендер бытовой",
        material="пластик, нержавеющая сталь",
        has_electric_motor=True,
        power_source="электричество",
        intended_use="бытовой",
        processing_degree="готовое изделие",
    )
    assert card.has_electric_motor is True


def test_section_result():
    result = SectionResult(
        candidates=[
            SectionCandidate(
                section="XVI",
                section_name="Машины",
                reasoning="Электродвигатель",
                exclusion_check="Не исключён",
                confidence=0.9,
            )
        ],
        selected_section="XVI",
        applied_rules=["ОПИ 1"],
    )
    assert result.selected_section == "XVI"
    assert result.backtrack_trigger is None


def test_backtrack_trigger():
    trigger = BacktrackTrigger(
        target_level=1,
        excluded_code="XVI",
        reason="Примечание 1е исключает",
    )
    assert trigger.target_level == 1


def test_decision_chain_entry():
    entry = DecisionChainEntry(
        level=2,
        code="85",
        name="Электрические машины",
        reasoning="Содержит электродвигатель",
        rules_applied=["ОПИ 1"],
        confidence=0.85,
    )
    assert entry.level == 2
    assert entry.confidence == 0.85
