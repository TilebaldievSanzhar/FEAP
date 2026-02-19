"""Level 1: Section classification (I-XXI)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.levels.base import BaseCascadeLevel, CascadeState
from feap.db.queries import get_all_section_notes, get_opi_rules, get_sections
from feap.llm.client import classify
from feap.prompts.assembler import PromptAssembler
from feap.schemas.common import DecisionChainEntry
from feap.schemas.section import SectionResult


class Level1Section(BaseCascadeLevel):
    level = 1

    def get_output_model(self):
        return SectionResult

    async def run(self, state: CascadeState, db: AsyncSession) -> SectionResult:
        sections = await get_sections(db)
        section_notes = await get_all_section_notes(db)
        opi_rules = await get_opi_rules(db, level=1)

        excluded = state.get_excluded(1)

        sections_data = [
            {"code": s.code, "name": s.name}
            for s in sections
            if s.code not in excluded
        ]
        notes_data = [
            {
                "scope_type": n.scope_type,
                "scope_code": n.scope_code,
                "note_number": n.note_number,
                "note_type": n.note_type,
                "note_text": n.note_text,
            }
            for n in section_notes
        ]
        rules_data = [
            {
                "rule_number": r.rule_number,
                "rule_text": r.rule_text,
                "prompt_instruction": r.prompt_instruction,
            }
            for r in opi_rules
        ]

        system, user = PromptAssembler.level1(
            card=state.product_card,
            sections=sections_data,
            section_notes=notes_data,
            opi_rules=rules_data,
        )

        result = await classify(
            level=1,
            system_prompt=system,
            user_message=user,
            output_model=SectionResult,
        )

        state.section = result.selected_section

        # Find section name
        section_name = ""
        for s in sections:
            if s.code == result.selected_section:
                section_name = s.name
                break

        state.add_decision(DecisionChainEntry(
            level=1,
            code=result.selected_section,
            name=section_name,
            reasoning=result.candidates[0].reasoning if result.candidates else "",
            rules_applied=result.applied_rules,
            confidence=result.candidates[0].confidence if result.candidates else 0.5,
        ))

        return result
