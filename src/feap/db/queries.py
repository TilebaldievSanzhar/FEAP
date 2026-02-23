"""Database queries for cascade context building."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from feap.db.models import ClassificationSession, ExplanatoryNote, OpiRule, TnvedNode, TnvedNote


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


# ── RAG: explanatory notes vector search ─────────────────────────────────────

async def search_explanatory_notes(
    session: AsyncSession,
    query_embedding: list[float],
    code_prefix: str | None = None,
    limit: int = 5,
) -> list[ExplanatoryNote]:
    """Vector similarity search over explanatory note chunks."""
    stmt = (
        select(ExplanatoryNote)
        .where(ExplanatoryNote.embedding.isnot(None))
        .order_by(ExplanatoryNote.embedding.cosine_distance(query_embedding))
        .limit(limit)
    )
    if code_prefix:
        stmt = stmt.where(ExplanatoryNote.code.startswith(code_prefix))
    result = await session.execute(stmt)
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
