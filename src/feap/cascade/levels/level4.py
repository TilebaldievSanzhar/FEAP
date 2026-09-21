"""Level 4: Subposition classification (6-digit code)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.levels.base import BaseCascadeLevel, CascadeState
from feap.cascade.selection import classify_choice, selected_candidate
from feap.cascade.evidence import evidence_context, evidence_prompt, evidence_records, evidence_validator, pause_for_evidence
from feap.config import settings
from feap.db.queries import (
    get_children,
    get_cross_group_definitions,
    get_explanations_for_position,
    get_node_by_code,
    get_opi_rules,
    get_section_definition_notes,
    get_subposition_notes,
)
from feap.llm.client import classify
from feap.prompts.assembler import PromptAssembler
from feap.schemas.common import DecisionChainEntry
from feap.schemas.subposition import EvidenceSubpositionResult, SubpositionResult


class Level4Subposition(BaseCascadeLevel):
    level = 4

    def get_output_model(self):
        return SubpositionResult if settings.SGR_EVIDENCE_POLICY == 'legacy' else EvidenceSubpositionResult

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

        # Targeted notes: subposition-level + definitions only (not all group notes)
        group_code = state.group
        subp_notes_rows = await get_subposition_notes(db, group_code)
        group_notes = [
            {"scope_type": n.scope_type, "scope_code": n.scope_code,
             "note_number": n.note_number, "note_type": n.note_type, "note_text": n.note_text}
            for n in subp_notes_rows
        ]

        # Section-level definitions (e.g., Section XI textile term definitions)
        section_code = pos_node.section_roman
        section_def_notes: list[dict] = []
        if section_code:
            section_rows = await get_section_definition_notes(db, section_code)
            section_def_notes = [
                {"scope_type": n.scope_type, "scope_code": n.scope_code,
                 "note_number": n.note_number, "note_type": n.note_type, "note_text": n.note_text}
                for n in section_rows
            ]

        # Cross-group definitions (e.g., Group 51 'wool' definition for Group 61)
        cross_def_rows = await get_cross_group_definitions(db, group_code)
        cross_def_notes = [
            {"scope_type": n.scope_type, "scope_code": n.scope_code,
             "note_number": n.note_number, "note_type": n.note_type, "note_text": n.note_text}
            for n in cross_def_rows
        ]

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
            group_notes=group_notes,
            section_notes=section_def_notes,
            cross_group_notes=cross_def_notes,
        )

        result = await classify_choice(
            classify_fn=classify,
            choices={"selected_subposition": {s.code for s in subpositions}},
            level=4,
            system_prompt=system + evidence_prompt(),
            user_message=user + evidence_context(state.product_description),
            output_model=self.get_output_model(),
            validate_result=evidence_validator(
                decision_field='selected_subposition',
                descriptions={s['code']: s['name'] for s in subpositions_data},
                source=state.product_description),
        )

        if result.backtrack_trigger:
            return result

        if pause_for_evidence(state, result):
            return result

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

        state.subposition = result.selected_subposition

        sp_name = ""
        for s in subpositions:
            if s.code == result.selected_subposition:
                sp_name = s.name
                break

        chosen = selected_candidate(result.candidates, "subposition", result.selected_subposition)
        state.add_decision(DecisionChainEntry(
            level=4, code=result.selected_subposition, name=sp_name,
            reasoning=chosen.reasoning if chosen else "",
            rules_applied=result.applied_rules,
            confidence=result.confidence,
            evidence_checks=evidence_records(result),
        ))

        return result
