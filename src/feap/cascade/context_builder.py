"""Context builder: assembles DB data for each cascade level.

This module provides convenience functions that combine multiple
DB queries into the context dictionaries needed by each level.
Most of the actual assembly is done in PromptAssembler;
this module handles the DB→dict conversion.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from feap.db.queries import (
    get_all_section_notes,
    get_groups_by_section,
    get_notes,
    get_opi_rules,
    get_sections,
)


async def build_section_context(db: AsyncSession) -> dict:
    """Build context for Level 1 (section classification)."""
    sections = await get_sections(db)
    notes = await get_all_section_notes(db)
    rules = await get_opi_rules(db, level=1)
    return {
        "sections": [{"code": s.code, "name": s.name} for s in sections],
        "section_notes": [_note_to_dict(n) for n in notes],
        "opi_rules": [_rule_to_dict(r) for r in rules],
    }


async def build_group_context(db: AsyncSession, section_roman: str) -> dict:
    """Build context for Level 2 (group classification)."""
    groups = await get_groups_by_section(db, section_roman)
    section_notes = await get_notes(db, "section", section_roman)
    rules = await get_opi_rules(db, level=2)

    group_codes = [g.code for g in groups]
    group_notes = []
    for gc in group_codes:
        gn = await get_notes(db, "group", gc)
        group_notes.extend(gn)

    return {
        "groups": [{"code": g.code, "name": g.name} for g in groups],
        "section_notes": [_note_to_dict(n) for n in section_notes],
        "group_notes": [_note_to_dict(n) for n in group_notes],
        "opi_rules": [_rule_to_dict(r) for r in rules],
    }


def _note_to_dict(n) -> dict:
    return {
        "scope_type": n.scope_type,
        "scope_code": n.scope_code,
        "note_number": n.note_number,
        "note_type": n.note_type,
        "note_text": n.note_text,
    }


def _rule_to_dict(r) -> dict:
    return {
        "rule_number": r.rule_number,
        "rule_text": r.rule_text,
        "prompt_instruction": r.prompt_instruction,
    }
