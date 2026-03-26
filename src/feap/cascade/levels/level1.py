"""Level 1: Section classification (I-XXI).

Two-step process (analogous to L3a/L3b):
  Step 1a — Shortlist: pick 2-3 candidate sections using ONLY section names.
  Step 1b — Verify: load notes for candidates only, check exclusions, pick final section.

This avoids sending all 52 section notes (~14K tokens) on every request.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.levels.base import BaseCascadeLevel, CascadeState
from feap.db.queries import get_opi_rules, get_sections, get_section_notes_for_codes
from feap.llm.client import classify
from feap.prompts.assembler import PromptAssembler
from feap.schemas.common import DecisionChainEntry
from feap.schemas.section import SectionResult, SectionShortlistResult

logger = logging.getLogger(__name__)


class Level1Section(BaseCascadeLevel):
    level = 1

    def get_output_model(self):
        return SectionResult

    async def run(self, state: CascadeState, db: AsyncSession) -> SectionResult:
        sections = await get_sections(db)
        excluded = state.get_excluded(1)
        excluded_reasons = state.get_excluded_reasons(1) or None

        sections_data = [
            {"code": s.code, "name": s.name}
            for s in sections
            if s.code not in excluded
        ]

        # ── Step 1a: Shortlist 2-3 candidates (no notes, cheap) ──
        sys_short, user_short = PromptAssembler.level1_shortlist(
            card=state.product_card,
            sections=sections_data,
            excluded_reasons=excluded_reasons,
        )

        shortlist = await classify(
            level=1,
            system_prompt=sys_short,
            user_message=user_short,
            output_model=SectionShortlistResult,
        )

        candidate_codes = [
            c for c in shortlist.shortlisted if c not in excluded
        ]
        if not candidate_codes:
            candidate_codes = [s["code"] for s in sections_data[:3]]

        logger.info(
            "L1 shortlist: %s (from %d sections)",
            candidate_codes, len(sections_data),
        )

        # ── Step 1b: Verify with notes for candidates only ──
        candidate_sections = [
            s for s in sections_data if s["code"] in candidate_codes
        ]

        section_notes = await get_section_notes_for_codes(db, candidate_codes)
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

        opi_rules = await get_opi_rules(db, level=1)
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
            sections=candidate_sections,
            section_notes=notes_data,
            opi_rules=rules_data,
            excluded_reasons=excluded_reasons,
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
