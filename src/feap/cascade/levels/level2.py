"""Level 2: Group classification (2-digit code)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.levels.base import BaseCascadeLevel, CascadeState
from feap.db.queries import (
    get_group_notes_for_section,
    get_groups_by_section,
    get_notes,
    get_opi_rules,
)
from feap.llm.client import classify
from feap.prompts.assembler import PromptAssembler
from feap.schemas.common import DecisionChainEntry
from feap.schemas.group import GroupResult


class Level2Group(BaseCascadeLevel):
    level = 2

    def get_output_model(self):
        return GroupResult

    async def run(self, state: CascadeState, db: AsyncSession) -> GroupResult:
        section = state.section
        excluded = state.get_excluded(2)
        excluded_reasons = state.get_excluded_reasons(2) or None

        groups = await get_groups_by_section(db, section)
        section_notes_rows = await get_notes(db, "section", section)
        group_notes_rows = await get_group_notes_for_section(db, section)
        opi_rules = await get_opi_rules(db, level=2)

        groups_data = [
            {"code": g.code, "name": g.name}
            for g in groups
            if g.code not in excluded
        ]
        section_notes = [
            {"scope_type": n.scope_type, "scope_code": n.scope_code,
             "note_number": n.note_number, "note_type": n.note_type, "note_text": n.note_text}
            for n in section_notes_rows
        ]
        group_notes = [
            {"scope_type": n.scope_type, "scope_code": n.scope_code,
             "note_number": n.note_number, "note_type": n.note_type, "note_text": n.note_text}
            for n in group_notes_rows
        ]
        rules_data = [
            {"rule_number": r.rule_number, "rule_text": r.rule_text,
             "prompt_instruction": r.prompt_instruction}
            for r in opi_rules
        ]

        system, user = PromptAssembler.level2(
            card=state.product_card,
            chain=[e.model_dump() for e in state.chain],
            section=section,
            groups=groups_data,
            section_notes=section_notes,
            group_notes=group_notes,
            opi_rules=rules_data,
            excluded_reasons=excluded_reasons,
        )

        result = await classify(
            level=2,
            system_prompt=system,
            user_message=user,
            output_model=GroupResult,
        )

        state.group = result.selected_group

        group_name = ""
        for g in groups:
            if g.code == result.selected_group:
                group_name = g.name
                break

        state.add_decision(DecisionChainEntry(
            level=2,
            code=result.selected_group,
            name=group_name,
            reasoning=result.candidates[0].reasoning if result.candidates else "",
            rules_applied=result.applied_rules,
            confidence=result.candidates[0].confidence if result.candidates else 0.5,
        ))

        return result
