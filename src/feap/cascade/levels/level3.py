"""Level 3: Position classification (4-digit code).

Two-step process:
  3a: Shortlist 2-5 candidates
  3b: Deep analysis with full context

С ``CASCADE_PRECEDENTS`` к кандидатам 3b добавляются позиции похожих решений
таможенных органов (feap.precedents), в том числе из других групп и разделов —
с их примечаниями и пояснениями. Если выбрана позиция другой группы, раздел и
группа состояния и цепочки приводятся к ней.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.levels.base import BaseCascadeLevel, CascadeState
from feap.cascade.selection import classify_choice, selected_candidate
from feap.config import settings
from feap.db.queries import get_children, get_explanations_by_codes, get_node_by_code, get_notes, get_opi_rules
from feap.llm.client import classify
from feap.precedents import Precedent
from feap.precedents import search as search_precedents
from feap.prompts.assembler import PromptAssembler
from feap.schemas.common import DecisionChainEntry
from feap.schemas.position import PositionResult, ShortlistResult

#: Сколько символов описания решения видит L3b.
PRECEDENT_CHARS = 400


def precedent_headings(precedents: Iterable[Precedent], known: set[str], excluded_positions: set[str],
                       excluded_groups: set[str]) -> list[str]:
    """Позиции похожих решений, которых нет среди позиций группы, по порядку сходства."""
    out: list[str] = []
    for p in precedents:
        heading = p.code[:4]
        if (len(heading) != 4 or heading in known or heading in out or heading in excluded_positions
                or heading[:2] in excluded_groups):
            continue
        out.append(heading)
    return out


def precedent_evidence(precedents: list[Precedent], n: int) -> list[dict]:
    """Блок «ПРЕЦЕДЕНТЫ КЛАССИФИКАЦИИ» для L3b."""
    out = []
    for p in precedents[:n]:
        text = p.description if len(p.description) <= PRECEDENT_CHARS else p.description[:PRECEDENT_CHARS] + '…'
        code = p.code.ljust(10, '0')
        out.append({'description': text, 'code': f'{code[:4]} {code[4:6]} {code[6:9]} {code[9]}',
                    'source': f'предварительное решение таможенного органа{f" ({p.country})" if p.country else ""}'})
    return out


def reroute(state: CascadeState, *, section: str, section_name: str, group: str, group_name: str,
            position: str, confidence: float, rules: list[str]) -> None:
    """Позиция выбрана в другой группе: раздел и группа — как у неё, в состоянии и цепочке."""
    reason = (f'Позиция {position} выбрана на уровне 3 среди кандидатов из похожих решений таможенных органов; '
              f'раздел и группа приведены к ней (прежний выбор: раздел {state.section}, группа {state.group}).')
    state.section, state.group = section, group
    replaced = {1: DecisionChainEntry(level=1, code=section, name=section_name, reasoning=reason,
                                      rules_applied=rules, confidence=confidence),
                2: DecisionChainEntry(level=2, code=group, name=group_name, reasoning=reason,
                                      rules_applied=rules, confidence=confidence)}
    state.chain = [replaced.get(e.level, e) for e in state.chain]


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

        # Precedents: headings of similar customs decisions join the candidates of 3b
        precedents: list[Precedent] = []
        extra_nodes = []
        if settings.CASCADE_PRECEDENTS:
            precedents = await search_precedents(db, state.product_description, settings.PRECEDENTS_CANDIDATES)
            state.precedent_codes = [p.code for p in precedents[:settings.PRECEDENTS_SHOWN]]
            for code in precedent_headings(precedents, {p["code"] for p in positions_data}, set(excluded),
                                           set(state.get_excluded(2))):
                node = await get_node_by_code(db, code)
                if node is not None and node.section_roman not in state.get_excluded(1):
                    extra_nodes.append(node)

        # Step 3a: Shortlist
        system_sl, user_sl = PromptAssembler.level3_shortlist(
            card=state.product_card,
            chain=[e.model_dump() for e in state.chain],
            group_code=group_code,
            positions=positions_data,
            group_notes=group_notes,
        )

        shortlist = await classify_choice(
            classify_fn=classify,
            choices={"shortlisted": {p["code"] for p in positions_data}},
            level=3,
            system_prompt=system_sl,
            user_message=user_sl,
            output_model=ShortlistResult,
        )

        # Step 3b: Deep analysis with RAG
        candidate_positions = [
            p for p in positions_data if p["code"] in shortlist.shortlisted
        ] + [{"code": n.code, "name": n.name} for n in extra_nodes]
        candidate_codes = [p["code"] for p in candidate_positions]

        notes = list(group_notes)
        if extra_nodes:
            for group in sorted({n.code[:2] for n in extra_nodes} - {group_code}):
                notes += [{"scope_type": n.scope_type, "scope_code": n.scope_code, "note_number": n.note_number,
                           "note_type": n.note_type, "note_text": n.note_text}
                          for n in await get_notes(db, "group", group)]
            sections = sorted({n.section_roman for n in extra_nodes if n.section_roman} | {state.section})
            if len(sections) > 1:
                # Кандидаты из разных разделов: исключения примечаний к разделам решают выбор
                for section in sections:
                    notes += [{"scope_type": n.scope_type, "scope_code": n.scope_code,
                               "note_number": n.note_number, "note_type": n.note_type, "note_text": n.note_text}
                              for n in await get_notes(db, "section", section)]

        # Direct loading: explanatory notes for candidate codes
        explanations_rows = await get_explanations_by_codes(db, candidate_codes)
        explanations = [
            {"code": e.code, "section_type": e.section_type, "title": e.title, "content": e.content}
            for e in explanations_rows
        ]

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
            group_notes=notes,
            explanations=explanations,
            opi_rules=rules_data,
            precedents=precedent_evidence(precedents, settings.PRECEDENTS_SHOWN) if precedents else None,
        )

        result = await classify_choice(
            classify_fn=classify,
            choices={"selected_position": {p["code"] for p in positions_data} | {n.code for n in extra_nodes}},
            level=3,
            system_prompt=system_deep,
            user_message=user_deep,
            output_model=PositionResult,
        )

        if result.backtrack_trigger:
            return result

        # If LLM signals missing parameters, propagate to state and stop
        if result.missing_parameters:
            state.missing_parameters = result.missing_parameters
            state.clarifying_question = (
                f"Для определения позиции необходимо уточнить: "
                f"{', '.join(result.missing_parameters)}"
            )
            return result

        chosen = selected_candidate(result.candidates, "position", result.selected_position)
        confidence = chosen.confidence if chosen else 0.5
        moved = next((n for n in extra_nodes if n.code == result.selected_position), None)
        if moved is not None:
            group_node = await get_node_by_code(db, moved.code[:2])
            section_node = await get_node_by_code(db, moved.section_roman) if moved.section_roman else None
            reroute(state, section=moved.section_roman or state.section,
                    section_name=section_node.name if section_node else "",
                    group=moved.code[:2], group_name=group_node.name if group_node else "",
                    position=moved.code, confidence=confidence, rules=result.applied_rules)

        state.position = result.selected_position

        pos_name = moved.name if moved is not None else ""
        for p in positions:
            if p.code == result.selected_position:
                pos_name = p.name
                break

        state.add_decision(DecisionChainEntry(
            level=3,
            code=result.selected_position,
            name=pos_name,
            reasoning=chosen.reasoning if chosen else "",
            rules_applied=result.applied_rules,
            confidence=confidence,
        ))

        return result
