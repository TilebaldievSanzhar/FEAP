"""Level 5: Subsubposition classification (10-digit code)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.levels.base import BaseCascadeLevel, CascadeState
from feap.db.queries import get_children, get_explanations_for_position, get_node_by_code
from feap.llm.client import classify
from feap.prompts.assembler import PromptAssembler
from feap.schemas.common import DecisionChainEntry
from feap.schemas.subsubposition import SubsubpositionResult


class Level5Subsubposition(BaseCascadeLevel):
    level = 5

    def get_output_model(self):
        return SubsubpositionResult

    async def run(self, state: CascadeState, db: AsyncSession) -> SubsubpositionResult:
        subposition_code = state.subposition

        sp_node = await get_node_by_code(db, subposition_code)
        if sp_node is None:
            raise ValueError(f"Subposition node not found: {subposition_code}")

        leaves = await get_children(db, sp_node.id)
        codes_data = [
            {"code": c.code, "name": c.name}
            for c in leaves
        ]

        # If only one leaf, auto-select
        if len(codes_data) == 1:
            leaf = codes_data[0]
            state.code_10 = leaf["code"]
            formatted = _format_code(leaf["code"])
            result = SubsubpositionResult(
                subposition=subposition_code,
                selected_code_8=leaf["code"][:8],
                selected_code_10=leaf["code"],
                reasoning="Единственный код в субпозиции",
                confidence=1.0,
                final_code=formatted,
            )
            state.add_decision(DecisionChainEntry(
                level=5, code=leaf["code"], name=leaf["name"],
                reasoning="Единственный код", rules_applied=["auto"],
                confidence=1.0,
            ))
            return result

        # Load explanatory notes for the position (same as L4)
        position_code = state.position
        explanations_rows = await get_explanations_for_position(db, position_code) if position_code else []
        explanations = [
            {"code": e.code, "section_type": e.section_type, "title": e.title, "content": e.content}
            for e in explanations_rows
        ]

        system, user = PromptAssembler.level5(
            card=state.product_card,
            chain=[e.model_dump() for e in state.chain],
            subposition_code=subposition_code,
            codes=codes_data,
            explanations=explanations,
        )

        result = await classify(
            level=5,
            system_prompt=system,
            user_message=user,
            output_model=SubsubpositionResult,
        )

        # Fallback: if LLM signals missing parameters but still picked a code,
        # accept the code with reduced confidence instead of stopping the cascade.
        # Only stop if LLM couldn't pick any code at all.
        if result.missing_parameters:
            if result.selected_code_10 and result.selected_code_10.strip():
                # LLM picked a best-guess code — accept it, cap confidence
                result.confidence = min(result.confidence, 0.5)
                result.missing_parameters = []  # clear so orchestrator won't stop
            else:
                # LLM truly couldn't decide — propagate needs_input
                state.missing_parameters = result.missing_parameters
                state.clarifying_question = (
                    f"Для выбора точного кода необходимо уточнить: "
                    f"{', '.join(result.missing_parameters)}"
                )
                return result

        state.code_10 = result.selected_code_10
        if not result.final_code:
            result.final_code = _format_code(result.selected_code_10)

        leaf_name = ""
        for c in leaves:
            if c.code == result.selected_code_10:
                leaf_name = c.name
                break

        state.add_decision(DecisionChainEntry(
            level=5, code=result.selected_code_10, name=leaf_name,
            reasoning=result.reasoning,
            rules_applied=[],
            confidence=result.confidence,
        ))

        return result


def _format_code(code: str) -> str:
    c = code.ljust(10, "0")
    return f"{c[:4]} {c[4:6]} {c[6:9]} {c[9]}"
