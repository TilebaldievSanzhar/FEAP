"""Financial impact metrics.

In customs classification, not all errors are equal:
- An error that changes the duty rate from 5% to 15% is critical.
- An error only at the 10-digit level while keeping the correct 6-digit
  subheading might mean the same duty rate — effectively harmless.

This module measures:
  - Duty rate correctness (boolean)
  - Duty rate delta in percentage points (signed and absolute)
  - Distribution of error severity (no impact / minor / major)
  - Estimated financial risk score

Note: Duty rates are parsed from strings like "15%", "7.5%", "0%", "ЕС",
"6.5 евро/кг". For composite rates (specific + ad valorem), only the
ad valorem component is used for comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from benchmark.schema.prediction import CaseResult
from benchmark.schema.test_case import TestCase

# Severity thresholds in percentage points
_MINOR_THRESHOLD = 2.0   # ≤ 2pp difference → minor
_MAJOR_THRESHOLD = 5.0   # > 5pp difference → major


@dataclass
class DutyRateDelta:
    case_id: str
    true_rate: float | None
    pred_rate: float | None
    delta: float | None      # predicted - true (positive = over-taxed)
    abs_delta: float | None
    severity: str            # "unknown" | "none" | "minor" | "major"


@dataclass
class FinancialMetrics:
    """Duty-rate level financial accuracy."""

    n_evaluable: int = 0        # cases where both true and pred rate are known
    n_unevaluable: int = 0      # cases where rate comparison is not possible

    # Correctness
    n_duty_correct: int = 0     # same ad valorem rate
    n_duty_wrong: int = 0

    # Severity distribution
    n_no_impact: int = 0        # same rate despite different code
    n_minor: int = 0            # <= minor_threshold pp
    n_major: int = 0            # > major_threshold pp

    # Delta statistics
    mean_abs_delta: float = 0.0
    max_abs_delta: float = 0.0
    total_abs_delta: float = 0.0

    # Per-case breakdown
    deltas: list[DutyRateDelta] = field(default_factory=list)

    @classmethod
    def compute(
        cls,
        cases: Sequence[TestCase],
        results: Sequence[CaseResult],
    ) -> "FinancialMetrics":
        metrics = cls()
        abs_deltas: list[float] = []

        for case, res in zip(cases, results, strict=True):
            true_rate_str = case.ground_truth.duty_rate
            # predicted duty rate: only available when heading is correct
            # and we can look it up — stored in CaseResult.duty_rate_correct
            # For now we use the boolean flag set by the comparator.
            if true_rate_str is None:
                metrics.n_unevaluable += 1
                metrics.deltas.append(
                    DutyRateDelta(
                        case_id=res.case_id,
                        true_rate=None,
                        pred_rate=None,
                        delta=None,
                        abs_delta=None,
                        severity="unknown",
                    )
                )
                continue

            true_pct = _parse_ad_valorem(true_rate_str)
            if true_pct is None:
                metrics.n_unevaluable += 1
                metrics.deltas.append(
                    DutyRateDelta(
                        case_id=res.case_id,
                        true_rate=None,
                        pred_rate=None,
                        delta=None,
                        abs_delta=None,
                        severity="unknown",
                    )
                )
                continue

            # If heading is wrong, we can't know the predicted duty rate from
            # the test case alone — mark as wrong but unknown delta.
            if not res.heading_match:
                metrics.n_evaluable += 1
                metrics.n_duty_wrong += 1
                metrics.deltas.append(
                    DutyRateDelta(
                        case_id=res.case_id,
                        true_rate=true_pct,
                        pred_rate=None,
                        delta=None,
                        abs_delta=None,
                        severity="unknown",
                    )
                )
                continue

            # Heading correct — duty rate should also be correct
            metrics.n_evaluable += 1
            if res.duty_rate_correct is True or res.subheading_match:
                metrics.n_duty_correct += 1
                metrics.n_no_impact += 1
                metrics.deltas.append(
                    DutyRateDelta(
                        case_id=res.case_id,
                        true_rate=true_pct,
                        pred_rate=true_pct,
                        delta=0.0,
                        abs_delta=0.0,
                        severity="none",
                    )
                )
                abs_deltas.append(0.0)
            else:
                metrics.n_duty_wrong += 1
                # Can't compute exact delta without the predicted duty rate here;
                # the runner should call enrich_with_duty_rates() to fill it in.
                metrics.deltas.append(
                    DutyRateDelta(
                        case_id=res.case_id,
                        true_rate=true_pct,
                        pred_rate=None,
                        delta=None,
                        abs_delta=None,
                        severity="unknown",
                    )
                )

        # Aggregate delta statistics from cases where delta is known
        known_abs = [d.abs_delta for d in metrics.deltas if d.abs_delta is not None]
        abs_deltas = known_abs
        if abs_deltas:
            metrics.total_abs_delta = sum(abs_deltas)
            metrics.mean_abs_delta = metrics.total_abs_delta / len(abs_deltas)
            metrics.max_abs_delta = max(abs_deltas)

        # Count severity from deltas
        for d in metrics.deltas:
            if d.abs_delta is None:
                pass  # already counted as unknown
            elif d.abs_delta == 0.0:
                pass  # already counted as no_impact
            elif d.abs_delta <= _MINOR_THRESHOLD:
                metrics.n_minor += 1
            else:
                metrics.n_major += 1

        return metrics

    def enrich_with_duty_rates(
        self,
        case_id_to_pred_rate: dict[str, str],
    ) -> None:
        """
        Fill in predicted duty rates after the fact (from a DB lookup).

        Call this after running evaluate.py if you have a duty-rate lookup dict
        keyed by case_id.
        """
        for delta in self.deltas:
            pred_str = case_id_to_pred_rate.get(delta.case_id)
            if pred_str is None or delta.true_rate is None:
                continue
            pred_pct = _parse_ad_valorem(pred_str)
            if pred_pct is None:
                continue
            delta.pred_rate = pred_pct
            delta.delta = pred_pct - delta.true_rate
            delta.abs_delta = abs(delta.delta)
            if delta.abs_delta == 0.0:
                delta.severity = "none"
                self.n_no_impact += 1
            elif delta.abs_delta <= _MINOR_THRESHOLD:
                delta.severity = "minor"
                self.n_minor += 1
            else:
                delta.severity = "major"
                self.n_major += 1

    @property
    def duty_accuracy(self) -> float:
        return self.n_duty_correct / self.n_evaluable if self.n_evaluable else 0.0

    def summary_lines(self) -> list[str]:
        return [
            "─── Financial / Duty-Rate Impact ────────────────────────",
            f"  Evaluable cases  : {self.n_evaluable}",
            f"  Unevaluable      : {self.n_unevaluable}  (no duty rate in ground truth)",
            f"  Duty correct     : {self.n_duty_correct} / {self.n_evaluable}"
            f"  ({self.duty_accuracy:.1%})",
            f"  No impact errors : {self.n_no_impact}",
            f"  Minor errors     : {self.n_minor}   (≤ {_MINOR_THRESHOLD}pp)",
            f"  Major errors     : {self.n_major}   (> {_MAJOR_THRESHOLD}pp)  ← critical",
            f"  Mean |Δ duty|    : {self.mean_abs_delta:.2f} pp",
            f"  Max  |Δ duty|    : {self.max_abs_delta:.2f} pp",
        ]


# ──────────────────────────────────────────────────────────────────────────────
# Duty rate parsing helpers
# ──────────────────────────────────────────────────────────────────────────────

_PCT_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


def _parse_ad_valorem(rate_str: str) -> float | None:
    """
    Extract the ad valorem percentage from a duty rate string.

    Examples:
        "15%"            → 15.0
        "7.5%"           → 7.5
        "0%"             → 0.0
        "5 евро/кг + 3%" → 3.0   (takes the percentage part)
        "ЕС"             → None  (can't parse)
        "без пошлины"    → 0.0
    """
    if not rate_str:
        return None
    rate_str = rate_str.strip().lower()

    if "без пошлины" in rate_str or rate_str in ("0", "free", "бесплатно"):
        return 0.0

    matches = _PCT_PATTERN.findall(rate_str)
    if not matches:
        return None

    # Take the last percentage in composite rates (the ad valorem part is usually last)
    try:
        return float(matches[-1].replace(",", "."))
    except ValueError:
        return None
