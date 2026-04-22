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
from feap.db.queries import (
    get_children,
    get_cross_group_definitions,
    get_explanations_for_position,
    get_node_by_code,
    get_section_definition_notes,
    get_subposition_notes,
)
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
        local_names = _compute_local_names(leaves)
        codes_data = [
            {"code": c.code, "name": local_names.get(c.code, c.name_full or c.name)}
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

        # Targeted notes: subposition-level + definitions only (not all group notes)
        group_code = state.group
        subp_notes_rows = await get_subposition_notes(db, group_code) if group_code else []
        group_notes = [
            {"scope_type": n.scope_type, "scope_code": n.scope_code,
             "note_number": n.note_number, "note_type": n.note_type, "note_text": n.note_text}
            for n in subp_notes_rows
        ]

        # Section-level definitions
        section_code = sp_node.section_roman
        section_def_notes: list[dict] = []
        if section_code:
            section_rows = await get_section_definition_notes(db, section_code)
            section_def_notes = [
                {"scope_type": n.scope_type, "scope_code": n.scope_code,
                 "note_number": n.note_number, "note_type": n.note_type, "note_text": n.note_text}
                for n in section_rows
            ]

        # Cross-group definitions
        cross_def_notes: list[dict] = []
        if group_code:
            cross_def_rows = await get_cross_group_definitions(db, group_code)
            cross_def_notes = [
                {"scope_type": n.scope_type, "scope_code": n.scope_code,
                 "note_number": n.note_number, "note_type": n.note_type, "note_text": n.note_text}
                for n in cross_def_rows
            ]

        chain = [e.model_dump() for e in state.chain]

        # Decide: two-step or single-step
        if len(codes_data) > SINGLE_STEP_THRESHOLD:
            # ── Step 5a: Branch selection (8-digit) ──
            shortlist = await self._step_branch(
                state, db, subposition_code, codes_data, chain, explanations, leaves,
                group_notes=group_notes,
                section_notes=section_def_notes,
                cross_group_notes=cross_def_notes,
            )
            # Multi-branch: use top-2 branches to prevent "invisible code" errors
            selected_branches = _get_top_branches(shortlist, max_branches=2)
            branch_codes = [c for c in codes_data if any(c["code"].startswith(b) for b in selected_branches)]
            if not branch_codes:
                logger.warning("L5a selected branches %s not found, falling back to all codes", selected_branches)
                branch_codes = codes_data
            else:
                logger.info(
                    "L5a selected branches %s (%d codes of %d total)",
                    selected_branches, len(branch_codes), len(codes_data),
                )

            # Auto-select only if single code across ALL selected branches
            if len(branch_codes) == 1:
                return self._auto_select(state, branch_codes[0], subposition_code)

            # ── Step 5b: Leaf selection within branch ──
            result = await self._step_leaf(
                state, subposition_code, branch_codes, chain, explanations,
                group_notes=group_notes,
                section_notes=section_def_notes,
                cross_group_notes=cross_def_notes,
            )
        else:
            # Single step for simple cases
            result = await self._step_leaf(
                state, subposition_code, codes_data, chain, explanations,
                group_notes=group_notes,
                section_notes=section_def_notes,
                cross_group_notes=cross_def_notes,
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
        group_notes: list[dict] | None = None,
        section_notes: list[dict] | None = None,
        cross_group_notes: list[dict] | None = None,
    ) -> SubsubpositionShortlistResult:
        """Step 5a: Select 8-digit branch. Returns full shortlist for multi-branch."""
        # Build branch data: group codes by 8-digit prefix
        branches_map: dict[str, list[dict]] = {}
        for c in codes_data:
            prefix8 = c["code"][:8]
            branches_map.setdefault(prefix8, []).append(c)

        # Build branch display data — show ALL leaf names per branch
        branches_display = []
        for code_8, branch_codes in sorted(branches_map.items()):
            # Extract branch name (common prefix) and leaf-specific texts
            leaf_items = []
            branch_name = ""
            for bc in branch_codes:
                name = bc["name"].replace(" 🠺 ", " > ")
                parts = name.split(" > ")
                if len(parts) > 1:
                    branch_name = branch_name or parts[0]
                    # Keep full sub-path (all intermediate levels)
                    leaf_items.append({"code": bc["code"], "text": " > ".join(parts[1:])})
                else:
                    leaf_items.append({"code": bc["code"], "text": name})

            # Build summary text
            if not branch_name:
                branch_name = leaf_items[0]["text"] if leaf_items else ""

            branches_display.append({
                "code_8": code_8,
                "branch_name": branch_name,
                "leaves": leaf_items,
                "leaf_count": len(branch_codes),
            })

        system, user = PromptAssembler.level5_branch(
            card=state.product_card,
            chain=chain,
            subposition_code=subposition_code,
            branches=branches_display,
            explanations=explanations,
            group_notes=group_notes,
            section_notes=section_notes,
            cross_group_notes=cross_group_notes,
        )

        shortlist = await classify(
            level=5,
            system_prompt=system,
            user_message=user,
            output_model=SubsubpositionShortlistResult,
        )

        logger.info("L5a branch shortlist: selected %s (conf=%.2f)", shortlist.selected_code_8, shortlist.confidence)
        return shortlist

    async def _step_leaf(
        self,
        state: CascadeState,
        subposition_code: str,
        codes_data: list[dict],
        chain: list[dict],
        explanations: list[dict],
        group_notes: list[dict] | None = None,
        section_notes: list[dict] | None = None,
        cross_group_notes: list[dict] | None = None,
    ) -> SubsubpositionResult:
        """Step 5b: Select final 10-digit code from (filtered) codes."""
        system, user = PromptAssembler.level5(
            card=state.product_card,
            chain=chain,
            subposition_code=subposition_code,
            codes=codes_data,
            explanations=explanations,
            group_notes=group_notes,
            section_notes=section_notes,
            cross_group_notes=cross_group_notes,
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


def _compute_local_names(leaves) -> dict[str, str]:
    """Strip the common 🠺 prefix from name_full, keeping only local hierarchy.

    10-digit codes store the full path in name_full:
      "Молоко... 🠺 в порошке... 🠺 без сахара 🠺 упаковка ≤ 2.5 кг"
    All siblings under the same subposition share the upper parts (position,
    subposition).  This function removes those shared parts so the model
    sees only the distinguishing local hierarchy:
      "без сахара 🠺 упаковка ≤ 2.5 кг"

    When branches have DIFFERENT prefixes (no global common prefix),
    the full name_full is preserved.  This is critical: the first 🠺-part
    carries branch-distinguishing context (e.g., "включающая видеозапись..."
    vs "прочая") that the L5a branch template uses for grouping.
    """
    SEP = " 🠺 "

    name_fulls: dict[str, str] = {}
    for c in leaves:
        nf = c.name_full or c.name or ""
        name_fulls[c.code] = nf

    # Split every name_full into parts; track only those that contain SEP
    parts_list = [nf.split(SEP) for nf in name_fulls.values() if SEP in nf]

    if not parts_list:
        # No hierarchy at all — return as-is
        return name_fulls

    # Find the longest common prefix (by number of 🠺 parts)
    # that is shared by ALL leaves having 🠺.  Keep at least 1 part.
    common_depth = 0
    min_len = min(len(p) for p in parts_list)
    for i in range(min_len - 1):
        if all(p[i] == parts_list[0][i] for p in parts_list):
            common_depth = i + 1
        else:
            break

    result: dict[str, str] = {}
    for code, nf in name_fulls.items():
        if SEP in nf:
            parts = nf.split(SEP)
            local = parts[common_depth:]
            result[code] = SEP.join(local) if local else parts[-1]
        else:
            result[code] = nf

    return result


def _get_top_branches(
    shortlist: SubsubpositionShortlistResult,
    max_branches: int = 2,
) -> list[str]:
    """Extract top-N distinct branches from L5a shortlist result."""
    branches: list[str] = [shortlist.selected_code_8]
    # Add runner-up candidates sorted by confidence
    for cand in sorted(shortlist.candidates, key=lambda c: c.confidence, reverse=True):
        if cand.code_8 not in branches:
            branches.append(cand.code_8)
        if len(branches) >= max_branches:
            break
    return branches


def _format_code(code: str) -> str:
    c = code.ljust(10, "0")
    return f"{c[:4]} {c[4:6]} {c[6:9]} {c[9]}"
