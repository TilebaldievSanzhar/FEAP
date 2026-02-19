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
    def level1(
        card: dict,
        sections: list[dict],
        section_notes: list[dict],
        opi_rules: list[dict],
    ) -> tuple[str, str]:
        """Level 1: Section classification."""
        system = render("system/level1_section.jinja2")

        parts = [
            render("context/product_card.jinja2", card=_to_obj(card)),
            render("context/tree_nodes.jinja2", title="РАЗДЕЛЫ ТН ВЭД", nodes=sections),
        ]
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
    ) -> tuple[str, str]:
        """Level 2: Group classification."""
        system = render("system/level2_group.jinja2", section=section)

        parts = [
            render("context/product_card.jinja2", card=_to_obj(card)),
            render("context/decision_chain.jinja2", chain=chain),
            render("context/tree_nodes.jinja2", title=f"ГРУППЫ РАЗДЕЛА {section}", nodes=groups),
        ]
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
        """Level 3b: Deep analysis of shortlisted positions."""
        system = render("system/level3_deep.jinja2", group_code=group_code)

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
    ) -> tuple[str, str]:
        """Level 4: Subposition classification."""
        system = render("system/level4_subposition.jinja2", position_code=position_code)

        parts = [
            render("context/product_card.jinja2", card=_to_obj(card)),
            render("context/decision_chain.jinja2", chain=chain),
            render("context/tree_nodes.jinja2", title=f"СУБПОЗИЦИИ ПОЗИЦИИ {position_code}", nodes=subpositions),
        ]
        if explanations:
            parts.append(render("context/explanatory.jinja2", explanations=explanations))
        if opi_rules:
            parts.append(render("context/opi_rules.jinja2", rules=opi_rules))
        if precedents:
            parts.append(render("context/precedents.jinja2", precedents=precedents))

        user = "\n\n".join(parts)
        return system, user

    @staticmethod
    def level5(
        card: dict,
        chain: list[dict],
        subposition_code: str,
        codes: list[dict],
        precedents: list[dict] | None = None,
    ) -> tuple[str, str]:
        """Level 5: Subsubposition (final 10-digit code)."""
        system = render("system/level5_subsubposition.jinja2", subposition_code=subposition_code)

        parts = [
            render("context/product_card.jinja2", card=_to_obj(card)),
            render("context/decision_chain.jinja2", chain=chain),
            render("context/tree_nodes.jinja2", title=f"КОДЫ СУБПОЗИЦИИ {subposition_code}", nodes=codes),
        ]
        if precedents:
            parts.append(render("context/precedents.jinja2", precedents=precedents))

        user = "\n\n".join(parts)
        return system, user


class _DictObj:
    """Helper to access dict keys as attributes for Jinja2 templates."""

    def __init__(self, d: dict):
        self.__dict__.update(d)

    def __getattr__(self, name: str) -> Any:
        return None


def _to_obj(d: dict) -> _DictObj:
    return _DictObj(d)
