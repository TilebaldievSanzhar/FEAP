"""Tests for backtracking logic."""

import pytest

from feap.cascade.backtracker import BacktrackLimitExceeded, apply_backtrack
from feap.cascade.levels.base import CascadeState
from feap.schemas.common import BacktrackTrigger, DecisionChainEntry


def test_apply_backtrack_resets_state():
    state = CascadeState("test")
    state.section = "XVI"
    state.group = "85"
    state.position = "8509"
    state.chain = [
        DecisionChainEntry(level=1, code="XVI", name="Машины", reasoning="...", confidence=0.9),
        DecisionChainEntry(level=2, code="85", name="Электрические", reasoning="...", confidence=0.8),
        DecisionChainEntry(level=3, code="8509", name="Бытовые", reasoning="...", confidence=0.7),
    ]

    trigger = BacktrackTrigger(
        target_level=2,
        excluded_code="85",
        reason="Примечание к группе 85 исключает",
    )

    target = apply_backtrack(state, trigger, attempt=0)

    assert target == 2
    assert state.group is None
    assert state.position is None
    assert "85" in state.get_excluded(2)
    assert len(state.chain) == 1  # Only level 1 remains


def test_backtrack_limit_exceeded():
    state = CascadeState("test")
    trigger = BacktrackTrigger(target_level=1, excluded_code="XVI", reason="test")

    with pytest.raises(BacktrackLimitExceeded):
        apply_backtrack(state, trigger, attempt=3)
