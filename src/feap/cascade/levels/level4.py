"""Level 4: Subposition classification (6-digit code)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.levels.base import BaseCascadeLevel, CascadeState
from feap.db.queries import get_children, get_node_by_code, get_opi_rules
from feap.llm.client import classify
from feap.prompts.assembler import PromptAssembler
from feap.rag.retriever import retrieve_explanations
from feap.schemas.common import DecisionChainEntry
from feap.schemas.subposition import SubpositionResult


class Level4Subposition(BaseCascadeLevel):
    level = 4

    def get_output_model(self):
        return SubpositionResult

    async def run(self, state: CascadeState, db: AsyncSession) -> SubpositionResult:
        position_code = state.position

        pos_node = await get_node_by_code(db, position_code)
        if pos_node is None:
            raise ValueError(f"Position node not found: {position_code}")

        subpositions = await get_children(db, pos_node.id)
        subpositions_data = [
            {"code": s.code, "name": s.name}
            for s in subpositions
        ]

        # If only one subposition, auto-select
        if len(subpositions_data) == 1:
            sp = subpositions_data[0]
            state.subposition = sp["code"]
            result = SubpositionResult(
                position=position_code,
                candidates=[],
                selected_subposition=sp["code"],
                applied_rules=["Auto-selected (single subposition)"],
                confidence=1.0,
            )
            state.add_decision(DecisionChainEntry(
                level=4, code=sp["code"], name=sp["name"],
                reasoning="Единственная субпозиция", rules_applied=["auto"],
                confidence=1.0,
            ))
            return result

        # RAG
        explanations = await retrieve_explanations(
            db,
            state.product_card.get("normalized_description", state.product_description),
            code_prefix=position_code,
            limit=5,
        )

        opi_rules = await get_opi_rules(db, level=4)
        rules_data = [
            {"rule_number": r.rule_number, "rule_text": r.rule_text,
             "prompt_instruction": r.prompt_instruction}
            for r in opi_rules
        ]

        system, user = PromptAssembler.level4(
            card=state.product_card,
            chain=[e.model_dump() for e in state.chain],
            position_code=position_code,
            subpositions=subpositions_data,
            explanations=explanations,
            opi_rules=rules_data,
        )

        result = await classify(
            level=4,
            system_prompt=system,
            user_message=user,
            output_model=SubpositionResult,
        )

        state.subposition = result.selected_subposition

        sp_name = ""
        for s in subpositions:
            if s.code == result.selected_subposition:
                sp_name = s.name
                break

        state.add_decision(DecisionChainEntry(
            level=4, code=result.selected_subposition, name=sp_name,
            reasoning=result.candidates[0].reasoning if result.candidates else "",
            rules_applied=result.applied_rules,
            confidence=result.confidence,
        ))

        return result
