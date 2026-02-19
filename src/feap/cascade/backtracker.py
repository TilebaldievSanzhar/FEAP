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
from feap.schemas.common import BacktrackTrigger

logger = logging.getLogger(__name__)

MAX_BACKTRACKS = 3


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
    if attempt >= MAX_BACKTRACKS:
        raise BacktrackLimitExceeded(
            f"Exceeded maximum backtracks ({MAX_BACKTRACKS}). "
            f"Last trigger: {trigger.reason}"
        )

    target_level = trigger.target_level
    logger.info(
        "Backtrack: level %d excluded '%s', reason: %s",
        target_level, trigger.excluded_code, trigger.reason,
    )

    # Record exclusion
    state.add_exclusion(target_level, trigger.excluded_code)

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
