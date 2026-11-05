"""Prompt assembler: builds system + user messages per cascade level."""

from __future__ import annotations

from typing import Any

from feap.prompts.registry import render


class PromptAssembler:
    """Assembles system prompt and user message for each cascade level."""

    @staticmethod
    def level0(description: str) -> tuple[str, str]:
        """Level 0: Product enrichment."""
        system = render("system/level0_enrichment.jinja2")
        user = f"Обогати описание следующего товара:\n\n{description}"
        return system, user

    @staticmethod
    def level1_shortlist(
        card: dict,
        sections: list[dict],
        excluded_reasons: list[dict] | None = None,
    ) -> tuple[str, str]:
        """Level 1a: Shortlist 2-3 candidate sections (no notes)."""
        system = render("system/level1_shortlist.jinja2")

        parts = [
            render("context/product_card.jinja2", card=_to_obj(card)),
            render("context/tree_nodes.jinja2", title="РАЗДЕЛЫ ТН ВЭД", nodes=sections),
        ]
        if excluded_reasons:
            parts.append(render("context/excluded.jinja2", excluded=excluded_reasons))

        user = "\n\n".join(parts)
        return system, user

    @staticmethod
    def level1(
        card: dict,
        sections: list[dict],
        section_notes: list[dict],
        opi_rules: list[dict],
        excluded_reasons: list[dict] | None = None,
    ) -> tuple[str, str]:
        """Level 1b: Verify candidates with section notes."""
        system = render("system/level1_section.jinja2")

        parts = [
            render("context/product_card.jinja2", card=_to_obj(card)),
            render("context/tree_nodes.jinja2", title="КАНДИДАТЫ (РАЗДЕЛЫ)", nodes=sections),
        ]
        if excluded_reasons:
            parts.append(render("context/excluded.jinja2", excluded=excluded_reasons))
        if section_notes:
            parts.append(render("context/notes.jinja2", notes=section_notes))
        if opi_rules:
            parts.append(render("context/opi_rules.jinja2", rules=opi_rules))

        user = "\n\n".join(parts)
        return system, user

    @staticmethod
    def level2(
        card: dict,
        chain: list[dict],
        section: str,
        groups: list[dict],
        section_notes: list[dict],
        group_notes: list[dict],
        opi_rules: list[dict],
        excluded_reasons: list[dict] | None = None,
    ) -> tuple[str, str]:
        """Level 2: Group classification."""
        system = render("system/level2_group.jinja2", section=section)

        parts = [
            render("context/product_card.jinja2", card=_to_obj(card)),
            render("context/decision_chain.jinja2", chain=chain),
            render("context/tree_nodes.jinja2", title=f"ГРУППЫ РАЗДЕЛА {section}", nodes=groups),
        ]
        if excluded_reasons:
            parts.append(render("context/excluded.jinja2", excluded=excluded_reasons))
        if section_notes:
            parts.append(render("context/notes.jinja2", notes=section_notes))
        if group_notes:
            parts.append(render("context/notes.jinja2", notes=group_notes))
        if opi_rules:
            parts.append(render("context/opi_rules.jinja2", rules=opi_rules))

        user = "\n\n".join(parts)
        return system, user

    @staticmethod
    def level3_shortlist(
        card: dict,
        chain: list[dict],
        group_code: str,
        positions: list[dict],
        group_notes: list[dict],
    ) -> tuple[str, str]:
        """Level 3a: Shortlist candidate positions."""
        system = render("system/level3_shortlist.jinja2", group_code=group_code)

        parts = [
            render("context/product_card.jinja2", card=_to_obj(card)),
            render("context/decision_chain.jinja2", chain=chain),
            render("context/tree_nodes.jinja2", title=f"ПОЗИЦИИ ГРУППЫ {group_code}", nodes=positions),
        ]
        if group_notes:
            parts.append(render("context/notes.jinja2", notes=group_notes))

        user = "\n\n".join(parts)
        return system, user

    @staticmethod
    def level3_deep(
        card: dict,
        chain: list[dict],
        group_code: str,
        candidates: list[dict],
        group_notes: list[dict],
        explanations: list[dict],
        opi_rules: list[dict],
        precedents: list[dict] | None = None,
    ) -> tuple[str, str]:
        """Level 3b: Deep analysis of shortlisted positions (and precedent headings, if any)."""
        system = render("system/level3_deep.jinja2", group_code=group_code, precedents=bool(precedents))

        parts = [
            render("context/product_card.jinja2", card=_to_obj(card)),
            render("context/decision_chain.jinja2", chain=chain),
            render("context/tree_nodes.jinja2", title="КАНДИДАТЫ (ПОЗИЦИИ)", nodes=candidates),
        ]
        if group_notes:
            parts.append(render("context/notes.jinja2", notes=group_notes))
        if explanations:
            parts.append(render("context/explanatory.jinja2", explanations=explanations))
        if opi_rules:
            parts.append(render("context/opi_rules.jinja2", rules=opi_rules))
        if precedents:
            parts.append(render("context/precedents.jinja2", precedents=precedents))

        user = "\n\n".join(parts)
        return system, user

    @staticmethod
    def level4(
        card: dict,
        chain: list[dict],
        position_code: str,
        subpositions: list[dict],
        explanations: list[dict],
        opi_rules: list[dict],
        precedents: list[dict] | None = None,
        group_notes: list[dict] | None = None,
        section_notes: list[dict] | None = None,
        cross_group_notes: list[dict] | None = None,
    ) -> tuple[str, str]:
        """Level 4: Subposition classification."""
        system = render("system/level4_subposition.jinja2", position_code=position_code)

        parts = [
            render("context/product_card.jinja2", card=_to_obj(card)),
            render("context/decision_chain_compact.jinja2", chain=chain),
            render("context/tree_nodes_l4.jinja2", position_code=position_code, nodes=subpositions),
        ]
        if explanations:
            parts.append(render("context/explanatory.jinja2", explanations=explanations))
        if opi_rules:
            parts.append(render("context/opi_rules.jinja2", rules=opi_rules))
        # Definitions first (section → cross-group → subposition notes)
        all_notes = _merge_definition_notes(section_notes, cross_group_notes, group_notes)
        if all_notes:
            parts.append(render("context/notes.jinja2", notes=all_notes))
        if precedents:
            parts.append(render("context/precedents.jinja2", precedents=precedents))

        user = "\n\n".join(parts)
        return system, user

    @staticmethod
    def level5_branch(
        card: dict,
        chain: list[dict],
        subposition_code: str,
        branches: list[dict],
        explanations: list[dict] | None = None,
        group_notes: list[dict] | None = None,
        section_notes: list[dict] | None = None,
        cross_group_notes: list[dict] | None = None,
    ) -> tuple[str, str]:
        """Level 5a: Branch (8-digit) selection."""
        system = render("system/level5_branch.jinja2", subposition_code=subposition_code)

        parts = [
            render("context/product_card.jinja2", card=_to_obj(card)),
            render("context/decision_chain_compact.jinja2", chain=chain),
            render("context/tree_branches_l5.jinja2", subposition_code=subposition_code, branches=branches),
        ]
        if explanations:
            parts.append(render("context/explanatory.jinja2", explanations=explanations))
        all_notes = _merge_definition_notes(section_notes, cross_group_notes, group_notes)
        if all_notes:
            parts.append(render("context/notes.jinja2", notes=all_notes))

        user = "\n\n".join(parts)
        return system, user

    @staticmethod
    def level5(
        card: dict,
        chain: list[dict],
        subposition_code: str,
        codes: list[dict],
        explanations: list[dict] | None = None,
        precedents: list[dict] | None = None,
        group_notes: list[dict] | None = None,
        section_notes: list[dict] | None = None,
        cross_group_notes: list[dict] | None = None,
    ) -> tuple[str, str]:
        """Level 5b: Final 10-digit code selection (within branch)."""
        system = render("system/level5_subsubposition.jinja2", subposition_code=subposition_code)

        parts = [
            render("context/product_card.jinja2", card=_to_obj(card)),
            render("context/decision_chain.jinja2", chain=chain),
            render("context/tree_nodes_l5.jinja2", subposition_code=subposition_code, nodes=codes),
        ]
        if explanations:
            parts.append(render("context/explanatory.jinja2", explanations=explanations))
        all_notes = _merge_definition_notes(section_notes, cross_group_notes, group_notes)
        if all_notes:
            parts.append(render("context/notes.jinja2", notes=all_notes))
        if precedents:
            parts.append(render("context/precedents.jinja2", precedents=precedents))

        user = "\n\n".join(parts)
        return system, user


def _merge_definition_notes(
    section_notes: list[dict] | None,
    cross_group_notes: list[dict] | None,
    group_notes: list[dict] | None,
) -> list[dict]:
    """Merge notes in priority order: section definitions → cross-group → subposition.

    Deduplicates by (scope_type, scope_code, note_number) to avoid repeats.
    """
    result: list[dict] = []
    seen: set[tuple] = set()
    for notes_list in (section_notes, cross_group_notes, group_notes):
        if not notes_list:
            continue
        for n in notes_list:
            key = (n.get("scope_type"), n.get("scope_code"), n.get("note_number"))
            if key not in seen:
                seen.add(key)
                result.append(n)
    return result


class _DictObj:
    """Helper to access dict keys as attributes for Jinja2 templates."""

    def __init__(self, d: dict):
        self.__dict__.update(d)

    def __getattr__(self, name: str) -> Any:
        return None


def _to_obj(d: dict) -> _DictObj:
    return _DictObj(d)
