"""Tests for prompt template rendering."""

from feap.prompts.assembler import PromptAssembler


def test_level0_prompt():
    system, user = PromptAssembler.level0("блендер Philips 600 Вт")
    assert "эксперт" in system
    assert "блендер" in user
    assert "Philips" in user


def test_level1_prompt():
    system, user = PromptAssembler.level1(
        card={"original_description": "блендер", "normalized_description": "блендер бытовой"},
        sections=[{"code": "XVI", "name": "Машины"}],
        section_notes=[],
        opi_rules=[],
    )
    assert "Раздел" in system or "раздел" in system.lower()
    assert "КАРТОЧКА ТОВАРА" in user
    assert "XVI" in user


def test_level3_shortlist_prompt():
    system, user = PromptAssembler.level3_shortlist(
        card={"original_description": "блендер", "normalized_description": "блендер бытовой"},
        chain=[],
        group_code="85",
        positions=[{"code": "8509", "name": "Машины электромеханические бытовые"}],
        group_notes=[],
    )
    assert "85" in system
    assert "8509" in user
