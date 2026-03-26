"""Level 5: Subsubposition classification (10-digit code).

Two-step process (analogous to L3a/L3b):
  Step 5a — Branch: select the 8-digit code group from all branches.
  Step 5b — Leaf: select the final 10-digit code within the chosen branch.

Single-step fallback when total codes ≤ 5 (simple enough for one pass).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.levels.base import BaseCascadeLevel, CascadeState
from feap.db.queries import get_children, get_explanations_for_position, get_node_by_code
from feap.llm.client import classify
from feap.prompts.assembler import PromptAssembler
from feap.schemas.common import DecisionChainEntry
from feap.schemas.subsubposition import (
    SubsubpositionCandidate,
    SubsubpositionResult,
    SubsubpositionShortlistResult,
)

logger = logging.getLogger(__name__)

# Threshold: if total leaf codes ≤ this, skip branch step
SINGLE_STEP_THRESHOLD = 5


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
            {"code": c.code, "name": c.name_full or c.name}
            for c in leaves
        ]

        # If only one leaf, auto-select
        if len(codes_data) == 1:
            return self._auto_select(state, codes_data[0], subposition_code)

        # Load explanatory notes
        position_code = state.position
        explanations_rows = await get_explanations_for_position(db, position_code) if position_code else []
        explanations = [
            {"code": e.code, "section_type": e.section_type, "title": e.title, "content": e.content}
            for e in explanations_rows
        ]

        chain = [e.model_dump() for e in state.chain]

        # Decide: two-step or single-step
        if len(codes_data) > SINGLE_STEP_THRESHOLD:
            # ── Step 5a: Branch selection (8-digit) ──
            selected_code_8 = await self._step_branch(
                state, db, subposition_code, codes_data, chain, explanations, leaves,
            )
            # Filter codes to selected branch
            branch_codes = [c for c in codes_data if c["code"].startswith(selected_code_8)]
            if not branch_codes:
                # Fallback: if branch selection hallucinated, use all codes
                logger.warning("L5a selected code_8 '%s' not found, falling back to all codes", selected_code_8)
                branch_codes = codes_data
            else:
                logger.info("L5a selected branch %s (%d codes of %d total)", selected_code_8, len(branch_codes), len(codes_data))

            # If branch has only one leaf, auto-select
            if len(branch_codes) == 1:
                return self._auto_select(state, branch_codes[0], subposition_code)

            # ── Step 5b: Leaf selection within branch ──
            result = await self._step_leaf(
                state, subposition_code, branch_codes, chain, explanations,
            )
        else:
            # Single step for simple cases
            result = await self._step_leaf(
                state, subposition_code, codes_data, chain, explanations,
            )

        # Post-process result
        return self._finalize(state, result, leaves, subposition_code)

    async def _step_branch(
        self,
        state: CascadeState,
        db: AsyncSession,
        subposition_code: str,
        codes_data: list[dict],
        chain: list[dict],
        explanations: list[dict],
        leaves,
    ) -> str:
        """Step 5a: Select 8-digit branch."""
        # Build branch data: group codes by 8-digit prefix
        branches_map: dict[str, list[dict]] = {}
        for c in codes_data:
            prefix8 = c["code"][:8]
            branches_map.setdefault(prefix8, []).append(c)

        # Build branch display data — show full name for each 8-digit group
        branches_display = []
        for code_8, branch_codes in sorted(branches_map.items()):
            # Combine leaf names to give full picture of the branch
            if len(branch_codes) == 1:
                text = branch_codes[0]["name"]
            else:
                # Show first leaf name + count
                text = branch_codes[0]["name"]

            # Clean up: replace 🠺 with readable arrow
            text = text.replace(" 🠺 ", " > ")

            branches_display.append({
                "code_8": code_8,
                "text": text,
                "leaf_count": len(branch_codes),
            })

        system, user = PromptAssembler.level5_branch(
            card=state.product_card,
            chain=chain,
            subposition_code=subposition_code,
            branches=branches_display,
            explanations=explanations,
        )

        shortlist = await classify(
            level=5,
            system_prompt=system,
            user_message=user,
            output_model=SubsubpositionShortlistResult,
        )

        logger.info("L5a branch shortlist: selected %s (conf=%.2f)", shortlist.selected_code_8, shortlist.confidence)
        return shortlist.selected_code_8

    async def _step_leaf(
        self,
        state: CascadeState,
        subposition_code: str,
        codes_data: list[dict],
        chain: list[dict],
        explanations: list[dict],
    ) -> SubsubpositionResult:
        """Step 5b: Select final 10-digit code from (filtered) codes."""
        system, user = PromptAssembler.level5(
            card=state.product_card,
            chain=chain,
            subposition_code=subposition_code,
            codes=codes_data,
            explanations=explanations,
        )

        return await classify(
            level=5,
            system_prompt=system,
            user_message=user,
            output_model=SubsubpositionResult,
        )

    def _auto_select(self, state: CascadeState, leaf: dict, subposition_code: str) -> SubsubpositionResult:
        """Auto-select when only one code available."""
        state.code_10 = leaf["code"]
        formatted = _format_code(leaf["code"])
        result = SubsubpositionResult(
            subposition=subposition_code,
            candidates=[SubsubpositionCandidate(
                code_10=leaf["code"],
                code_text=leaf["name"],
                match_type="direct",
                reasoning="Единственный код в субпозиции/ветке",
                confidence=1.0,
            )],
            selected_code_8=leaf["code"][:8],
            selected_code_10=leaf["code"],
            reasoning="Единственный код в субпозиции/ветке",
            confidence=1.0,
            final_code=formatted,
        )
        state.add_decision(DecisionChainEntry(
            level=5, code=leaf["code"], name=leaf["name"],
            reasoning="Единственный код", rules_applied=["auto"],
            confidence=1.0,
        ))
        return result

    def _finalize(
        self,
        state: CascadeState,
        result: SubsubpositionResult,
        leaves,
        subposition_code: str,
    ) -> SubsubpositionResult:
        """Post-process: handle missing params, validate code, update state."""
        # Fallback: accept best-guess even with missing parameters
        if result.missing_parameters:
            if result.selected_code_10 and result.selected_code_10.strip():
                result.confidence = min(result.confidence, 0.5)
                result.missing_parameters = []
            else:
                state.missing_parameters = result.missing_parameters
                state.clarifying_question = (
                    f"Для выбора точного кода необходимо уточнить: "
                    f"{', '.join(result.missing_parameters)}"
                )
                return result

        # Validate that selected code exists
        valid_codes = {c.code for c in leaves}
        if result.selected_code_10 not in valid_codes:
            prefix8 = result.selected_code_10[:8] if result.selected_code_10 else ""
            candidates = [c for c in leaves if c.code.startswith(prefix8)]
            if not candidates:
                prefix6 = result.selected_code_10[:6] if result.selected_code_10 else ""
                candidates = [c for c in leaves if c.code.startswith(prefix6)]
            if candidates:
                result.selected_code_10 = candidates[-1].code
                result.selected_code_8 = result.selected_code_10[:8]
                result.confidence = min(result.confidence, 0.4)

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
