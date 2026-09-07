"""Comparator: maps (TestCase, CasePrediction) → CaseResult.

Determines match at every hierarchy level and detects system behaviour
flags (trap codes, correct/wrong stops, backtrack effectiveness).
"""

from __future__ import annotations

from benchmark.schema.prediction import CasePrediction, CaseResult
from benchmark.schema.test_case import TestCase

# Ordered level names — used to find the first error level
_LEVELS = ["section", "group", "heading", "subheading", "code_8", "code_10"]


class Comparator:
    """Stateless comparator: one method, pure function logic."""

    def compare(self, case: TestCase, pred: CasePrediction) -> CaseResult:
        gt = case.ground_truth

        # ── Match booleans ────────────────────────────────────────────
        section_match = _eq(pred.predicted_section, gt.section)
        group_match = _eq(pred.predicted_group, gt.group)
        heading_match = _eq(pred.predicted_heading, gt.heading)
        subheading_match = _eq(pred.predicted_subheading, gt.subheading)
        code_8_match = _eq(pred.predicted_code_8, gt.code_8)
        code_10_match = _eq(pred.predicted_code_10, gt.code_10)

        # ── First error level ─────────────────────────────────────────
        matches = {
            "section": section_match,
            "group": group_match,
            "heading": heading_match,
            "subheading": subheading_match,
            "code_8": code_8_match,
            "code_10": code_10_match,
        }
        first_error_level: str | None = None
        for lvl in _LEVELS:
            if not matches[lvl]:
                first_error_level = lvl
                break

        # ── Hierarchical score ────────────────────────────────────────
        from benchmark.metrics.hierarchical import LEVEL_WEIGHTS

        hierarchical_score = sum(
            LEVEL_WEIGHTS[lvl] * float(matches[lvl]) for lvl in _LEVELS
        )

        # ── Trap code ─────────────────────────────────────────────────
        fell_into_trap = False
        if case.trap_codes and pred.predicted_code_10:
            normalized_traps = {t.replace(" ", "") for t in case.trap_codes}
            fell_into_trap = pred.predicted_code_10 in normalized_traps

        # ── Clarification behaviour ───────────────────────────────────
        system_stopped = pred.status == "needs_input"
        correctly_stopped = case.requires_clarification and system_stopped
        wrongly_stopped = (not case.requires_clarification) and system_stopped

        # ── Duty rate ─────────────────────────────────────────────────
        # We can only be sure the duty rate is correct if the subheading matched
        # (6-digit level determines the rate under the Harmonised System).
        duty_rate_correct: bool | None = None
        if gt.duty_rate is not None:
            if pred.status == "completed":
                duty_rate_correct = subheading_match
            else:
                duty_rate_correct = False

        return CaseResult(
            case_id=case.id,
            difficulty=case.difficulty.value,
            section=gt.section,
            section_match=section_match,
            group_match=group_match,
            heading_match=heading_match,
            subheading_match=subheading_match,
            code_8_match=code_8_match,
            code_10_match=code_10_match,
            hierarchical_score=hierarchical_score,
            first_error_level=first_error_level,
            fell_into_trap=fell_into_trap,
            confidence=pred.predicted_confidence,
            status=pred.status,
            correctly_stopped=correctly_stopped,
            wrongly_stopped=wrongly_stopped,
            duty_rate_correct=duty_rate_correct,
            latency_ms=pred.latency_ms,
        )

    def compare_all(
        self,
        cases: list[TestCase],
        predictions: list[CasePrediction],
    ) -> list[CaseResult]:
        assert len(cases) == len(predictions)
        return [self.compare(c, p) for c, p in zip(cases, predictions, strict=True)]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _eq(a: str | None, b: str | None) -> bool:
    """Case-insensitive, whitespace-stripped equality."""
    if a is None or b is None:
        return False
    return a.strip().upper() == b.strip().upper()
