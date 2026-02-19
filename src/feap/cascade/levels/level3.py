"""Level 3: Position classification (4-digit code).

Two-step process:
  3a: Shortlist 2-5 candidates
  3b: Deep analysis with full context
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.levels.base import BaseCascadeLevel, CascadeState
from feap.db.queries import get_children, get_node_by_code, get_notes, get_opi_rules
from feap.llm.client import classify
from feap.prompts.assembler import PromptAssembler
from feap.rag.retriever import retrieve_explanations
from feap.schemas.common import DecisionChainEntry
from feap.schemas.position import PositionResult, ShortlistResult


class Level3Position(BaseCascadeLevel):
    level = 3

    def get_output_model(self):
        return PositionResult

    async def run(self, state: CascadeState, db: AsyncSession) -> PositionResult:
        group_code = state.group
        excluded = state.get_excluded(3)

        # Get group node and its children (positions)
        group_node = await get_node_by_code(db, group_code)
        if group_node is None:
            raise ValueError(f"Group node not found: {group_code}")

        positions = await get_children(db, group_node.id)
        positions_data = [
            {"code": p.code, "name": p.name}
            for p in positions
            if p.code not in excluded
        ]

        # Get group notes
        group_notes_rows = await get_notes(db, "group", group_code)
        group_notes = [
            {"scope_type": n.scope_type, "scope_code": n.scope_code,
             "note_number": n.note_number, "note_type": n.note_type, "note_text": n.note_text}
            for n in group_notes_rows
        ]

        # Step 3a: Shortlist
        system_sl, user_sl = PromptAssembler.level3_shortlist(
            card=state.product_card,
            chain=[e.model_dump() for e in state.chain],
            group_code=group_code,
            positions=positions_data,
            group_notes=group_notes,
        )

        shortlist = await classify(
            level=3,
            system_prompt=system_sl,
            user_message=user_sl,
            output_model=ShortlistResult,
        )

        # Step 3b: Deep analysis with RAG
        candidate_positions = [
            p for p in positions_data if p["code"] in shortlist.shortlisted
        ]

        # RAG: retrieve explanatory notes for candidates
        explanations = await retrieve_explanations(
            db,
            state.product_card.get("normalized_description", state.product_description),
            code_prefix=group_code,
            limit=8,
        )

        opi_rules = await get_opi_rules(db, level=3)
        rules_data = [
            {"rule_number": r.rule_number, "rule_text": r.rule_text,
             "prompt_instruction": r.prompt_instruction}
            for r in opi_rules
        ]

        system_deep, user_deep = PromptAssembler.level3_deep(
            card=state.product_card,
            chain=[e.model_dump() for e in state.chain],
            group_code=group_code,
            candidates=candidate_positions,
            group_notes=group_notes,
            explanations=explanations,
            opi_rules=rules_data,
        )

        result = await classify(
            level=3,
            system_prompt=system_deep,
            user_message=user_deep,
            output_model=PositionResult,
        )

        state.position = result.selected_position

        pos_name = ""
        for p in positions:
            if p.code == result.selected_position:
                pos_name = p.name
                break

        state.add_decision(DecisionChainEntry(
            level=3,
            code=result.selected_position,
            name=pos_name,
            reasoning=result.candidates[0].reasoning if result.candidates else "",
            rules_applied=result.applied_rules,
            confidence=result.candidates[0].confidence if result.candidates else 0.5,
        ))

        return result
