"""Post-classification validation.

Programmatic checks (no LLM needed):
1. Code existence — verify the 10-digit code exists in the DB
2. Chain integrity — code_10 → subposition → position → group → section
3. Duty rates and measures
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.levels.base import CascadeState
from feap.db.queries import get_ancestors, get_leaf_by_code
from feap.schemas.common import DecisionChainEntry
from feap.schemas.validation import ClassificationChain, ClassificationResult

logger = logging.getLogger(__name__)


async def validate_and_build_result(
    state: CascadeState,
    db: AsyncSession,
) -> ClassificationResult:
    """Validate the classification and build the final result."""
    code_10 = state.code_10
    if not code_10:
        raise ValueError("No final code in state")

    # 1. Check code exists
    leaf = await get_leaf_by_code(db, code_10)
    if leaf is None:
        logger.warning("Code %s not found in DB", code_10)
        # Try to find closest match
        risk_flags = [f"Код {code_10} не найден в актуальной номенклатуре"]
    else:
        risk_flags = []

    # 2. Chain integrity
    chain_entries = state.chain
    chain_ok = _verify_chain_integrity(chain_entries, code_10)
    if not chain_ok:
        risk_flags.append("Нарушена целостность цепочки классификации")

    # 3. Build classification chain
    chain = _build_chain(chain_entries)

    # 4. Collect applied OPI rules
    all_rules = set()
    for entry in chain_entries:
        all_rules.update(entry.rules_applied)

    # 5. Determine confidence
    min_confidence = min((e.confidence for e in chain_entries), default=0.5)

    # 6. Get duty rate
    duty_rate = None
    if leaf:
        duty_rate = leaf.duty_rate

    formatted = _format_code(code_10)

    return ClassificationResult(
        final_code=code_10,
        code_formatted=formatted,
        code_description=leaf.name if leaf else "",
        duty_rate=duty_rate,
        classification_chain=chain,
        opi_applied=sorted(all_rules),
        confidence=min_confidence,
        risk_flags=risk_flags,
        requires_human_review=min_confidence < 0.6 or bool(risk_flags),
    )


def _verify_chain_integrity(chain: list[DecisionChainEntry], code_10: str) -> bool:
    """Verify that codes in the chain are consistent with the final code."""
    if not chain:
        return False

    # Expected hierarchy:
    # section (roman) -> group (2 digits) -> position (4 digits) ->
    # subposition (6 digits) -> code_10 (10 digits)
    for entry in chain:
        if entry.level == 2:  # group
            if code_10[:2] != entry.code:
                return False
        elif entry.level == 3:  # position
            if code_10[:4] != entry.code:
                return False
        elif entry.level == 4:  # subposition
            if code_10[:6] != entry.code:
                return False
        elif entry.level == 5:  # code_10
            if code_10 != entry.code:
                return False
    return True


def _build_chain(entries: list[DecisionChainEntry]) -> ClassificationChain:
    """Build ClassificationChain from decision entries."""
    by_level = {e.level: e for e in entries}

    def _get(level: int) -> DecisionChainEntry:
        return by_level.get(level, DecisionChainEntry(
            level=level, code="?", name="?", reasoning="Not determined",
            rules_applied=[], confidence=0.0,
        ))

    return ClassificationChain(
        section=_get(1),
        group=_get(2),
        position=_get(3),
        subposition=_get(4),
        code_10=_get(5),
    )


def _format_code(code: str) -> str:
    c = code.ljust(10, "0")
    return f"{c[:4]} {c[4:6]} {c[6:9]} {c[9]}"
