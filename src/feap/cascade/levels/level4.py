"""Level 4: Subposition classification (6-digit code)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.levels.base import BaseCascadeLevel, CascadeState
from feap.db.queries import get_children, get_explanations_for_position, get_node_by_code, get_opi_rules
from feap.llm.client import classify
from feap.prompts.assembler import PromptAssembler
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
            from feap.schemas.subposition import SubpositionCandidate
            result = SubpositionResult(
                position=position_code,
                candidates=[SubpositionCandidate(
                    subposition=sp["code"],
                    subposition_text=sp["name"],
                    reasoning="Единственная субпозиция в данной позиции",
                    confidence=1.0,
                )],
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

        # Direct loading: explanatory notes for position
        explanations_rows = await get_explanations_for_position(db, position_code)
        explanations = [
            {"code": e.code, "section_type": e.section_type, "title": e.title, "content": e.content}
            for e in explanations_rows
        ]

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

        # Fallback: if LLM signals missing parameters but still picked a subposition,
        # accept the subposition with reduced confidence instead of stopping.
        if result.missing_parameters:
            if result.selected_subposition and result.selected_subposition.strip():
                # LLM picked a best-guess subposition — accept it, cap confidence
                result.confidence = min(result.confidence, 0.5)
                result.missing_parameters = []
            else:
                state.missing_parameters = result.missing_parameters
                state.clarifying_question = (
                    f"Для определения субпозиции необходимо уточнить: "
                    f"{', '.join(result.missing_parameters)}"
                )
                return result

        # Validate that the selected subposition exists among available options
        valid_codes = {s.code for s in subpositions}
        if result.selected_subposition not in valid_codes:
            # Try to find closest match by 4-digit prefix
            prefix4 = result.selected_subposition[:4] if result.selected_subposition else ""
            candidates = [s for s in subpositions if s.code.startswith(prefix4)]
            if candidates:
                result.selected_subposition = candidates[-1].code
                result.confidence = min(result.confidence, 0.4)

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
