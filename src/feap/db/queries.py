"""Database queries for cascade context building."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import datetime as dt

from feap.db.models import (
    ClassificationSession,
    ExciseRate,
    ExplanatoryNote,
    NtmMeasure,
    NtmMeasureCode,
    NtmMeasureType,
    OpiRule,
    TnvedNode,
    TnvedNote,
    VatExemption,
)


# ── Tree navigation ──────────────────────────────────────────────────────────

async def get_sections(session: AsyncSession) -> list[TnvedNode]:
    """Return all 21 sections (level 0)."""
    result = await session.execute(
        select(TnvedNode).where(TnvedNode.level == 0).order_by(TnvedNode.id)
    )
    return list(result.scalars().all())


async def get_children(session: AsyncSession, parent_id: int) -> list[TnvedNode]:
    """Return direct children of a node."""
    result = await session.execute(
        select(TnvedNode).where(TnvedNode.parent_id == parent_id).order_by(TnvedNode.code)
    )
    return list(result.scalars().all())


async def get_groups_by_section(session: AsyncSession, section_roman: str) -> list[TnvedNode]:
    """Return all groups (level 1) belonging to a section."""
    result = await session.execute(
        select(TnvedNode)
        .where(TnvedNode.level == 1, TnvedNode.section_roman == section_roman)
        .order_by(TnvedNode.code)
    )
    return list(result.scalars().all())


async def get_node_by_code(session: AsyncSession, code: str) -> TnvedNode | None:
    """Find a node by its code."""
    result = await session.execute(
        select(TnvedNode).where(TnvedNode.code == code).limit(1)
    )
    return result.scalar_one_or_none()


async def get_leaf_by_code(session: AsyncSession, code: str) -> TnvedNode | None:
    """Find a leaf node by its full 10-digit code."""
    result = await session.execute(
        select(TnvedNode).where(TnvedNode.code == code, TnvedNode.is_leaf.is_(True)).limit(1)
    )
    return result.scalar_one_or_none()


async def get_ancestors(session: AsyncSession, node: TnvedNode) -> list[TnvedNode]:
    """Walk up the tree and return ancestors from root to node (inclusive)."""
    chain: list[TnvedNode] = [node]
    current = node
    while current.parent_id is not None:
        result = await session.execute(
            select(TnvedNode).where(TnvedNode.id == current.parent_id)
        )
        parent = result.scalar_one_or_none()
        if parent is None:
            break
        chain.append(parent)
        current = parent
    chain.reverse()
    return chain


# ── Notes ────────────────────────────────────────────────────────────────────

async def get_notes(
    session: AsyncSession,
    scope_type: str,
    scope_code: str,
) -> list[TnvedNote]:
    """Get notes for a section or group."""
    result = await session.execute(
        select(TnvedNote)
        .where(TnvedNote.scope_type == scope_type, TnvedNote.scope_code == scope_code)
        .order_by(TnvedNote.id)
    )
    return list(result.scalars().all())


async def get_all_section_notes(session: AsyncSession) -> list[TnvedNote]:
    """Get all section-level notes."""
    result = await session.execute(
        select(TnvedNote).where(TnvedNote.scope_type == "section").order_by(TnvedNote.id)
    )
    return list(result.scalars().all())


async def get_section_notes_for_codes(
    session: AsyncSession, section_codes: list[str]
) -> list[TnvedNote]:
    """Get section notes only for specific sections (by Roman numeral)."""
    if not section_codes:
        return []
    result = await session.execute(
        select(TnvedNote)
        .where(TnvedNote.scope_type == "section", TnvedNote.scope_code.in_(section_codes))
        .order_by(TnvedNote.id)
    )
    return list(result.scalars().all())


async def get_group_notes_for_section(
    session: AsyncSession, section_roman: str
) -> list[TnvedNote]:
    """Get notes for all groups within a section."""
    groups = await get_groups_by_section(session, section_roman)
    group_codes = [g.code for g in groups]
    if not group_codes:
        return []
    result = await session.execute(
        select(TnvedNote)
        .where(TnvedNote.scope_type == "group", TnvedNote.scope_code.in_(group_codes))
        .order_by(TnvedNote.id)
    )
    return list(result.scalars().all())


# ── OPI rules ────────────────────────────────────────────────────────────────

async def get_opi_rules(session: AsyncSession, level: int | None = None) -> list[OpiRule]:
    """Get OPI rules, optionally filtered by applicable cascade level."""
    stmt = select(OpiRule).order_by(OpiRule.priority)
    if level is not None:
        stmt = stmt.where(OpiRule.applicable_levels.contains([level]))
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ── Explanatory notes: direct loading by code ────────────────────────────────

async def get_explanations_by_codes(
    session: AsyncSession,
    codes: list[str],
) -> list[ExplanatoryNote]:
    """Get full explanatory note sections for a list of position codes (L3b)."""
    if not codes:
        return []
    result = await session.execute(
        select(ExplanatoryNote)
        .where(ExplanatoryNote.code.in_(codes))
        .order_by(ExplanatoryNote.code, ExplanatoryNote.section_type)
    )
    return list(result.scalars().all())


async def get_explanations_for_position(
    session: AsyncSession,
    code: str,
) -> list[ExplanatoryNote]:
    """Get explanatory note sections for a single position code (L4)."""
    result = await session.execute(
        select(ExplanatoryNote)
        .where(ExplanatoryNote.code == code)
        .order_by(ExplanatoryNote.section_type)
    )
    return list(result.scalars().all())


# ── VAT exemptions ──────────────────────────────────────────────────────────

async def get_vat_exemption(
    session: AsyncSession,
    code: str,
) -> VatExemption | None:
    """Find the best matching VAT exemption for a 10-digit code.

    Tries prefixes from longest (exact match) to shortest.
    Only returns exemptions with valid date range (or no dates set).
    """
    today = dt.date.today()
    # Generate prefixes: '3004201000', '300420100', ..., '30', '3'
    prefixes = [code[:i] for i in range(len(code), 0, -1)]

    result = await session.execute(
        select(VatExemption).where(
            VatExemption.code_pattern.in_(prefixes),
            (VatExemption.valid_from.is_(None)) | (VatExemption.valid_from <= today),
            (VatExemption.valid_to.is_(None)) | (VatExemption.valid_to >= today),
        )
    )
    rows = list(result.scalars().all())
    if not rows:
        return None

    # Return the most specific match (longest code_pattern)
    rows.sort(key=lambda r: len(r.code_pattern), reverse=True)
    return rows[0]


# ── Excise rates ────────────────────────────────────────────────────────────

async def get_excise_rate(
    session: AsyncSession,
    code: str,
) -> ExciseRate | None:
    """Find the best matching excise rate for a 10-digit code.

    Same prefix-match logic as get_vat_exemption: longest matching pattern wins.
    Returns None if the product is not subject to excise tax.
    """
    today = dt.date.today()
    prefixes = [code[:i] for i in range(len(code), 0, -1)]

    result = await session.execute(
        select(ExciseRate).where(
            ExciseRate.code_pattern.in_(prefixes),
            (ExciseRate.valid_from.is_(None)) | (ExciseRate.valid_from <= today),
            (ExciseRate.valid_to.is_(None)) | (ExciseRate.valid_to >= today),
        )
    )
    rows = list(result.scalars().all())
    if not rows:
        return None

    rows.sort(key=lambda r: len(r.code_pattern), reverse=True)
    return rows[0]


# ── Non-tariff measures (NTM) ──────────────────────────────────────────────

async def get_ntm_measures_for_code(
    session: AsyncSession,
    code: str,
) -> list[tuple[NtmMeasure, NtmMeasureCode, NtmMeasureType]]:
    """Get all active NTM measures applicable to a 10-digit code.

    Checks exact match + all parent prefixes (8, 6, 4, 2 digits) with
    applies_to_children=True. Filters by valid date range.
    """
    today = dt.date.today()

    # Build prefix list: '8509800000', '85098000', '850980', '8509', '85'
    prefixes = []
    for length in (10, 8, 6, 4, 2):
        if length <= len(code):
            prefixes.append(code[:length])

    stmt = (
        select(NtmMeasure, NtmMeasureCode, NtmMeasureType)
        .join(NtmMeasureCode, NtmMeasure.id == NtmMeasureCode.measure_id)
        .join(NtmMeasureType, NtmMeasure.measure_type_id == NtmMeasureType.id)
        .where(
            # Code match: exact or parent with applies_to_children
            (
                (NtmMeasureCode.code == code)
                | (
                    NtmMeasureCode.code.in_(prefixes)
                    & NtmMeasureCode.applies_to_children.is_(True)
                )
            ),
            # Measure validity
            NtmMeasure.valid_from <= today,
            (NtmMeasure.valid_to.is_(None)) | (NtmMeasure.valid_to >= today),
            # Code-level validity
            (NtmMeasureCode.valid_from.is_(None)) | (NtmMeasureCode.valid_from <= today),
            (NtmMeasureCode.valid_to.is_(None)) | (NtmMeasureCode.valid_to >= today),
        )
        .order_by(NtmMeasureType.kind, NtmMeasure.id)
    )

    result = await session.execute(stmt)
    return list(result.all())


async def get_ntm_measure_types(session: AsyncSession) -> list[NtmMeasureType]:
    """Get all NTM measure types."""
    result = await session.execute(
        select(NtmMeasureType).order_by(NtmMeasureType.kind)
    )
    return list(result.scalars().all())


# ── Session persistence ─────────────────────────────────────────────────────

async def save_session(
    session: AsyncSession,
    session_id: str,
    state_dict: dict,
) -> None:
    """Upsert a classification session state."""
    result = await session.execute(
        select(ClassificationSession).where(
            ClassificationSession.session_id == session_id
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.state = state_dict
    else:
        session.add(ClassificationSession(session_id=session_id, state=state_dict))
    await session.commit()


async def load_session(
    session: AsyncSession,
    session_id: str,
) -> dict | None:
    """Load a classification session state. Returns None if not found."""
    result = await session.execute(
        select(ClassificationSession).where(
            ClassificationSession.session_id == session_id
        )
    )
    row = result.scalar_one_or_none()
    return row.state if row else None


async def delete_session(
    session: AsyncSession,
    session_id: str,
) -> None:
    """Delete a classification session after successful completion."""
    result = await session.execute(
        select(ClassificationSession).where(
            ClassificationSession.session_id == session_id
        )
    )
    row = result.scalar_one_or_none()
    if row:
        await session.delete(row)
        await session.commit()
