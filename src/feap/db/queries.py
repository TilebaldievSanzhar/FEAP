"""Database queries for cascade context building."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import datetime as dt

from feap.clock import today_bishkek
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
    TradeAgreement,
    TradeRemedy,
    VatExemption,
)
from feap.tariff.agreements import TradeAgreementRef, agreement_from_row
from feap.tariff.remedies import RemedyMeasure, measure_from_row


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


async def get_subposition_notes(
    session: AsyncSession,
    group_code: str,
) -> list[TnvedNote]:
    """Get only subposition-level and additional notes for a group.

    Returns notes with note_number starting with 'субп.' or 'доп.',
    plus any definition-type notes. These are the notes most relevant
    for L4/L5 classification (subposition and sub-subposition levels),
    filtering out the general group notes that add noise.
    """
    result = await session.execute(
        select(TnvedNote)
        .where(
            TnvedNote.scope_type == "group",
            TnvedNote.scope_code == group_code,
            (
                TnvedNote.note_number.like("субп.%")
                | TnvedNote.note_number.like("доп.%")
                | (TnvedNote.note_type == "definition")
            ),
        )
        .order_by(TnvedNote.id)
    )
    return list(result.scalars().all())


async def get_section_definition_notes(
    session: AsyncSession,
    section_code: str,
) -> list[TnvedNote]:
    """Get definition-type notes for a section.

    Returns only notes with note_type='definition' — pure term definitions
    that apply across the section (e.g., Section XI Note 7 defines 'готовые').
    Inclusive notes are excluded to avoid noise (they often contain
    position-specific rules that are irrelevant for L4/L5).

    Cross-group definitions (e.g., Group 51 'wool' definition) are loaded
    separately by get_cross_group_definitions().
    """
    result = await session.execute(
        select(TnvedNote)
        .where(
            TnvedNote.scope_type == "section",
            TnvedNote.scope_code == section_code,
            TnvedNote.note_type == "definition",
        )
        .order_by(TnvedNote.id)
    )
    return list(result.scalars().all())


async def get_cross_group_definitions(
    session: AsyncSession,
    group_code: str,
) -> list[TnvedNote]:
    """Get terminology definitions from related groups that apply globally.

    Some groups define terms for the entire Nomenclature (e.g., Group 51
    Note 1: 'wool means sheep/lamb wool'). When classifying in a related
    group (e.g., 61-63 for clothing), these definitions are essential.

    Returns definition notes from source groups where the note text
    contains 'Номенклатур' (indicating global scope).
    """
    # Mapping: target group -> source groups with global definitions
    CROSS_GROUP_MAP: dict[str, list[str]] = {
        # Textiles: groups 50-63 need definitions from group 51 (wool/hair)
        # and group 54 (man-made fibres)
        **{str(g): ["51", "54"] for g in range(50, 64)},
        # Rubber: groups 40-41 share definitions
        "41": ["40", "41"],
        "42": ["41", "43"],
        "43": ["43"],
        # Plastics: group 39 definitions apply to 39-40
        "40": ["39"],
        # Metals: group 72 definitions relevant for 72-83
        **{str(g): ["72"] for g in range(73, 84)},
    }

    source_groups = CROSS_GROUP_MAP.get(group_code, [])
    if not source_groups:
        return []

    # Exclude own group (those notes are loaded separately)
    source_groups = [g for g in source_groups if g != group_code]
    if not source_groups:
        return []

    result = await session.execute(
        select(TnvedNote)
        .where(
            TnvedNote.scope_type == "group",
            TnvedNote.scope_code.in_(source_groups),
            (
                (TnvedNote.note_type == "definition")
                | TnvedNote.note_number.like("1")
            ),
            TnvedNote.note_text.like("%Номенклатур%"),
        )
        .order_by(TnvedNote.scope_code, TnvedNote.id)
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

async def get_vat_rules(
    session: AsyncSession,
    code: str,
    on_date: dt.date | None = None,
) -> tuple[VatExemption | None, list[VatExemption]]:
    """Льготы НДС для 10-значного кода на дату.

    Возвращает (безусловная льгота с самым длинным префиксом | None,
    условные льготы — от самой специфичной). Условные (condition IS NOT NULL)
    применяются только по выбору декларанта (ShipmentItemIn.vat_benefit).
    """
    day = on_date or today_bishkek()
    # Generate prefixes: '3004201000', '300420100', ..., '30', '3'
    prefixes = [code[:i] for i in range(len(code), 0, -1)]

    result = await session.execute(
        select(VatExemption).where(
            VatExemption.code_pattern.in_(prefixes),
            (VatExemption.valid_from.is_(None)) | (VatExemption.valid_from <= day),
            (VatExemption.valid_to.is_(None)) | (VatExemption.valid_to >= day),
        )
    )
    rows = sorted(result.scalars().all(), key=lambda r: len(r.code_pattern), reverse=True)
    unconditional = next((r for r in rows if not r.condition), None)
    conditional = [r for r in rows if r.condition]
    return unconditional, conditional


async def get_vat_exemption(
    session: AsyncSession,
    code: str,
    on_date: dt.date | None = None,
) -> VatExemption | None:
    """Безусловная льгота НДС с самым длинным совпавшим префиксом (или None)."""
    unconditional, _ = await get_vat_rules(session, code, on_date)
    return unconditional


# ── Тарифные меры: защита рынка ЕАЭС и торговые режимы ─────────────────────

async def get_trade_remedies(session: AsyncSession) -> list[RemedyMeasure]:
    """Все не истёкшие меры защиты рынка (таблица — десятки строк)."""
    result = await session.execute(
        select(TradeRemedy).where(TradeRemedy.status != "expired")
    )
    return [
        measure_from_row({
            "measure_no": r.measure_no, "kind": r.kind, "title": r.title,
            "countries": r.countries, "codes": r.codes, "rates": r.rates,
            "status": r.status, "valid_from": r.valid_from, "valid_to": r.valid_to,
            "product_description": r.product_description, "notes": r.notes,
            "legal_basis": r.legal_basis, "needs_review": r.needs_review,
            "source_url": r.source_url,
        })
        for r in result.scalars().all()
    ]


async def get_trade_agreements(session: AsyncSession) -> list[TradeAgreementRef]:
    result = await session.execute(select(TradeAgreement).order_by(TradeAgreement.country))
    return [
        agreement_from_row({
            "country": r.country, "regime": r.regime, "agreement": r.agreement,
            "status": r.status, "in_force_from": r.in_force_from,
            "in_force_to": r.in_force_to, "origin_proof": r.origin_proof,
            "legal_ref": r.legal_ref, "note": r.note, "verified": r.verified,
        })
        for r in result.scalars().all()
    ]


# ── Excise rates ────────────────────────────────────────────────────────────

async def get_excise_rate(
    session: AsyncSession,
    code: str,
    on_date: dt.date | None = None,
) -> ExciseRate | None:
    """Find the best matching excise rate for a 10-digit code.

    Same prefix-match logic as get_vat_exemption: longest matching pattern wins.
    Returns None if the product is not subject to excise tax.
    """
    today = on_date or today_bishkek()
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
    today = today_bishkek()

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
    org_id=None,
) -> None:
    """Upsert a classification session state.

    org_id — владелец сессии; при обновлении существующей сессии не меняется
    (продолжение не может «переписать» владельца).
    """
    result = await session.execute(
        select(ClassificationSession).where(
            ClassificationSession.session_id == session_id
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.state = state_dict
        if existing.org_id is None and org_id is not None:
            existing.org_id = org_id
    else:
        session.add(ClassificationSession(
            session_id=session_id, state=state_dict, org_id=org_id,
        ))
    await session.commit()


async def load_session_row(
    session: AsyncSession,
    session_id: str,
) -> ClassificationSession | None:
    result = await session.execute(
        select(ClassificationSession).where(
            ClassificationSession.session_id == session_id
        )
    )
    return result.scalar_one_or_none()


async def load_session(
    session: AsyncSession,
    session_id: str,
) -> dict | None:
    """Load a classification session state. Returns None if not found."""
    row = await load_session_row(session, session_id)
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
