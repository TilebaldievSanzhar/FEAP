"""Confidence calibration metrics.

A well-calibrated classifier should satisfy:
    P(correct | confidence = p) ≈ p   for all p ∈ [0, 1]

We compute:
  - ECE  (Expected Calibration Error)   — lower is better
  - MCE  (Maximum Calibration Error)    — worst-case bin
  - ACE  (Average Calibration Error)    — equal-weight bins variant
  - Reliability diagram data            — for plotting

Reference: Guo et al. (2017) "On Calibration of Modern Neural Networks"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from benchmark.schema.prediction import CaseResult

_DEFAULT_BINS = 10


@dataclass
class CalibrationBin:
    lower: float
    upper: float
    count: int = 0
    correct: int = 0
    confidence_sum: float = 0.0

    @property
    def fraction_correct(self) -> float:
        return self.correct / self.count if self.count else 0.0

    @property
    def mean_confidence(self) -> float:
        return self.confidence_sum / self.count if self.count else 0.0

    @property
    def gap(self) -> float:
        """Signed gap: positive = overconfident."""
        return self.mean_confidence - self.fraction_correct


@dataclass
class CalibrationMetrics:
    """Full calibration report."""

    n_with_confidence: int = 0     # cases where system returned a confidence score
    n_without_confidence: int = 0  # cases where system couldn't (needs_input / error)

    ece: float = 0.0   # Expected Calibration Error
    mce: float = 0.0   # Maximum Calibration Error
    ace: float = 0.0   # Average Calibration Error

    # Per-bin data for reliability diagram
    bins: list[CalibrationBin] = field(default_factory=list)

    # Overconfidence / underconfidence summary
    n_overconfident: int = 0   # conf > accuracy in bin
    n_underconfident: int = 0  # conf < accuracy in bin

    @classmethod
    def compute(
        cls,
        results: Sequence[CaseResult],
        n_bins: int = _DEFAULT_BINS,
        use_heading: bool = True,
    ) -> "CalibrationMetrics":
        """
        Compute calibration metrics.

        Args:
            results: Evaluation results with confidence and heading_match.
            n_bins:  Number of equal-width bins in [0, 1].
            use_heading: If True, use heading_match (4-digit) as correctness signal.
                         If False, use code_10_match.
        """
        metrics = cls()

        # Initialise bins
        width = 1.0 / n_bins
        bins = [
            CalibrationBin(lower=i * width, upper=(i + 1) * width)
            for i in range(n_bins)
        ]

        for res in results:
            if res.confidence is None:
                metrics.n_without_confidence += 1
                continue
            metrics.n_with_confidence += 1

            correct = res.heading_match if use_heading else res.code_10_match
            conf = max(0.0, min(1.0, res.confidence))

            # Assign to bin
            bin_idx = min(int(conf * n_bins), n_bins - 1)
            b = bins[bin_idx]
            b.count += 1
            b.correct += int(correct)
            b.confidence_sum += conf

        non_empty = [b for b in bins if b.count > 0]
        n_total = metrics.n_with_confidence

        if n_total == 0:
            metrics.bins = bins
            return metrics

        # ECE: weighted average gap
        ece = sum(
            (b.count / n_total) * abs(b.gap) for b in non_empty
        )
        metrics.ece = ece

        # MCE: maximum gap across bins
        metrics.mce = max((abs(b.gap) for b in non_empty), default=0.0)

        # ACE: unweighted average gap (each non-empty bin counts equally)
        metrics.ace = (
            sum(abs(b.gap) for b in non_empty) / len(non_empty)
            if non_empty
            else 0.0
        )

        metrics.bins = bins
        metrics.n_overconfident = sum(1 for b in non_empty if b.gap > 0)
        metrics.n_underconfident = sum(1 for b in non_empty if b.gap < 0)

        return metrics

    # ------------------------------------------------------------------ display

    def summary_lines(self) -> list[str]:
        lines = [
            "─── Confidence Calibration ──────────────────────────────",
            f"  Cases with confidence   : {self.n_with_confidence}",
            f"  Cases without confidence: {self.n_without_confidence}",
            f"  ECE  (Expected)         : {self.ece:.4f}   ← primary KPI (lower=better)",
            f"  MCE  (Maximum)          : {self.mce:.4f}",
            f"  ACE  (Average)          : {self.ace:.4f}",
            f"  Overconfident bins      : {self.n_overconfident}",
            f"  Underconfident bins     : {self.n_underconfident}",
        ]
        return lines

    def reliability_diagram_data(self) -> list[dict]:
        """Return data suitable for plotting a reliability diagram."""
        return [
            {
                "bin_lower": b.lower,
                "bin_upper": b.upper,
                "mean_confidence": b.mean_confidence,
                "fraction_correct": b.fraction_correct,
                "count": b.count,
                "gap": b.gap,
            }
            for b in self.bins
            if b.count > 0
        ]

    def reliability_diagram_ascii(self, width: int = 50) -> list[str]:
        """ASCII reliability diagram (fraction_correct vs mean_confidence)."""
        lines = [
            "─── Reliability Diagram (● = actual, · = perfect) ───────",
            "  1.0 ┤",
        ]
        n_rows = 10
        for row in range(n_rows, 0, -1):
            threshold = row / n_rows
            row_str = f"  {threshold:.1f} ┤"
            for b in self.bins:
                if b.count == 0:
                    row_str += " "
                    continue
                perfect_y = (b.lower + b.upper) / 2
                actual_y = b.fraction_correct
                if abs(perfect_y - threshold) < (0.5 / n_rows):
                    row_str += "·"
                elif abs(actual_y - threshold) < (0.5 / n_rows):
                    row_str += "●"
                else:
                    row_str += " "
            lines.append(row_str)
        lines.append("  0.0 ┼" + "─" * self.bins.__len__())
        lines.append("       " + "".join(f"{b.mean_confidence:.0f}"[0] for b in self.bins))
        return lines
