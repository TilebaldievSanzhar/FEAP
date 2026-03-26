"""Backtracking logic for cascade classification.

When a level discovers an exclusion (e.g., group note says "not including X"),
it returns a backtrack_trigger. The orchestrator uses this module to:
1. Record the excluded option
2. Trim the decision chain
3. Reset state to the target level
"""

from __future__ import annotations

import logging

from feap.cascade.levels.base import CascadeState
from feap.config import settings
from feap.schemas.common import BacktrackTrigger

logger = logging.getLogger(__name__)


class BacktrackLimitExceeded(Exception):
    pass


def apply_backtrack(state: CascadeState, trigger: BacktrackTrigger, attempt: int) -> int:
    """Apply backtracking and return the level to restart from.

    Args:
        state: Current cascade state
        trigger: The backtrack trigger from LLM
        attempt: Current backtrack attempt number

    Returns:
        Level number to restart from
    """
    # Infer correct target_level from excluded_code length
    # (LLM often sets wrong target_level)
    target_level = _infer_target_level(trigger)

    # Proactive escalation for L2 → L1:
    # If backtrack targets L2 (group) and the excluded group code doesn't
    # belong to the current section, the problem is the SECTION choice —
    # escalate to L1 immediately instead of retrying groups.
    if target_level == 2 and state.section:
        excluded_group = _normalize_excluded_code(trigger.excluded_code)
        # Escalate to L1 if:
        # (a) There are already exclusions at L2 (we've been going in circles), OR
        # (b) LLM references a group that was never chosen at L2 (e.g., "group 09
        #     not found" — meaning the group belongs to a different section entirely)
        existing_exclusions_l2 = state.get_excluded(2)
        chosen_groups = {e.code for e in state.chain if e.level == 2}
        if existing_exclusions_l2 or (excluded_group and excluded_group not in chosen_groups):
            parent_code = state.section
            if parent_code and parent_code not in state.get_excluded(1):
                logger.info(
                    "Proactive escalation L2→L1: section '%s' likely wrong "
                    "(excluded_group='%s', existing_exclusions=%s), "
                    "escalating to L1",
                    parent_code, excluded_group, existing_exclusions_l2,
                )
                target_level = 1
                trigger = BacktrackTrigger(
                    target_level=1,
                    excluded_code=parent_code,
                    reason=f"Section escalation: {trigger.reason}",
                )

    # General escalation: if the same level was already backtracked,
    # escalate one level up (e.g., group→section).
    # Check this BEFORE the limit so escalation gets a chance to resolve.
    existing_exclusions = state.get_excluded(target_level)
    if existing_exclusions and target_level > 1:
        parent_level = target_level - 1
        parent_code = _get_parent_code(state, target_level)
        if parent_code and parent_code not in state.get_excluded(parent_level):
            logger.info(
                "Escalating backtrack: level %d already has exclusions %s, "
                "escalating to level %d, excluding '%s'",
                target_level, existing_exclusions, parent_level, parent_code,
            )
            target_level = parent_level
            trigger = BacktrackTrigger(
                target_level=parent_level,
                excluded_code=parent_code,
                reason=f"Escalated: {trigger.reason}",
            )

    if attempt >= settings.CASCADE_MAX_BACKTRACK:
        raise BacktrackLimitExceeded(
            f"Exceeded maximum backtracks ({settings.CASCADE_MAX_BACKTRACK}). "
            f"Last trigger: {trigger.reason}"
        )

    logger.info(
        "Backtrack: level %d excluded '%s', reason: %s",
        target_level, trigger.excluded_code, trigger.reason,
    )

    # Record exclusion (use normalized code to match DB codes)
    normalized_code = _normalize_excluded_code(trigger.excluded_code)
    state.add_exclusion(target_level, normalized_code, reason=trigger.reason)

    # Trim decision chain to target level (remove everything from target_level onward)
    state.chain = [e for e in state.chain if e.level < target_level]

    # Reset state fields for levels >= target_level
    level_reset = {
        1: ["section", "group", "position", "subposition", "code_10"],
        2: ["group", "position", "subposition", "code_10"],
        3: ["position", "subposition", "code_10"],
        4: ["subposition", "code_10"],
        5: ["code_10"],
    }

    for field in level_reset.get(target_level, []):
        setattr(state, field, None)

    return target_level


def _infer_target_level(trigger: BacktrackTrigger) -> int:
    """Infer the correct target level from the excluded code format."""
    code = _normalize_excluded_code(trigger.excluded_code)

    if not code or not code[0].isdigit():
        # Roman numeral or non-numeric → section (level 1)
        return 1
    if len(code) <= 2:
        # 2-digit code → group (level 2)
        return 2
    if len(code) <= 4:
        # 4-digit code → position (level 3)
        return 3
    # Longer → subposition or below
    return max(trigger.target_level, 4)


def _normalize_excluded_code(raw: str) -> str:
    """Normalize LLM-generated excluded code to match DB codes.

    LLM sometimes returns '96xx', '96.05', '96 05', '9605, 9617' etc.
    For numeric codes: strip to digits only (take first code if comma-separated).
    For section codes (Roman numerals like 'XX', 'XVI'): keep as-is.
    """
    stripped = raw.strip()
    # Handle comma-separated lists — take the first code
    if "," in stripped:
        stripped = stripped.split(",")[0].strip()
    # If it looks like a Roman numeral section code, keep as-is
    if stripped and all(c in "IVXivx" for c in stripped):
        return stripped.upper()
    return "".join(c for c in stripped if c.isdigit())


def _get_parent_code(state: CascadeState, level: int) -> str | None:
    """Get the current parent code for a given level."""
    if level == 2:
        return state.section  # escalate group→section
    if level == 3:
        return state.group    # escalate position→group
    if level == 4:
        return state.position
    return None
