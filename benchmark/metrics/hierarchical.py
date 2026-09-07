"""Hierarchical accuracy metrics for ТН ВЭД classification.

The most important metrics, in order:
  1. heading_accuracy    (4 digits) — determines tariff schedule applicability
  2. subheading_accuracy (6 digits) — determines duty rate under HS
  3. code_10_accuracy    (10 digits) — national EAEU code

Weights for the composite hierarchical_score are calibrated to match the
importance of each level in real-world customs practice.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence

from benchmark.schema.prediction import CaseResult
from benchmark.schema.test_case import Difficulty, TestCase

# Weights: must sum to 1.0
# Heading (4-digit) carries the most weight because it's the primary
# classification unit under the Harmonised System.
LEVEL_WEIGHTS = {
    "section":    0.05,
    "group":      0.10,
    "heading":    0.40,  # ★ primary KPI
    "subheading": 0.25,  # determines duty rate
    "code_8":     0.10,
    "code_10":    0.10,
}
assert abs(sum(LEVEL_WEIGHTS.values()) - 1.0) < 1e-9


@dataclass
class LevelStats:
    level: str
    correct: int = 0
    total: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def error_rate(self) -> float:
        return 1.0 - self.accuracy

    def __str__(self) -> str:
        return f"{self.level:12s}: {self.accuracy:.3f}  ({self.correct}/{self.total})"


@dataclass
class HierarchicalMetrics:
    """Accuracy at every hierarchy level + composite score."""

    section: LevelStats = field(default_factory=lambda: LevelStats("section"))
    group: LevelStats = field(default_factory=lambda: LevelStats("group"))
    heading: LevelStats = field(default_factory=lambda: LevelStats("heading"))
    subheading: LevelStats = field(default_factory=lambda: LevelStats("subheading"))
    code_8: LevelStats = field(default_factory=lambda: LevelStats("code_8"))
    code_10: LevelStats = field(default_factory=lambda: LevelStats("code_10"))

    # Per-difficulty breakdowns
    by_difficulty: dict[str, "HierarchicalMetrics"] = field(default_factory=dict)

    # Per-section breakdowns
    by_section: dict[str, "HierarchicalMetrics"] = field(default_factory=dict)

    @property
    def hierarchical_score(self) -> float:
        """Weighted composite score across all levels."""
        return sum(
            LEVEL_WEIGHTS[lvl] * getattr(self, lvl).accuracy
            for lvl in LEVEL_WEIGHTS
        )

    @property
    def heading_accuracy(self) -> float:
        return self.heading.accuracy

    @property
    def subheading_accuracy(self) -> float:
        return self.subheading.accuracy

    @property
    def code_10_accuracy(self) -> float:
        return self.code_10.accuracy

    # ------------------------------------------------------------------ build

    @classmethod
    def compute(
        cls,
        cases: Sequence[TestCase],
        results: Sequence[CaseResult],
    ) -> "HierarchicalMetrics":
        """Compute metrics over the full result set plus breakdowns."""
        assert len(cases) == len(results), "cases and results must align"

        overall = cls()
        by_diff: dict[str, HierarchicalMetrics] = {}
        by_sect: dict[str, HierarchicalMetrics] = {}

        for case, res in zip(cases, results, strict=True):
            # ── overall ────────────────────────────────────────────────────
            _accumulate(overall, res)

            # ── by difficulty ──────────────────────────────────────────────
            d = case.difficulty.value
            if d not in by_diff:
                by_diff[d] = cls()
            _accumulate(by_diff[d], res)

            # ── by section ─────────────────────────────────────────────────
            s = case.ground_truth.section
            if s not in by_sect:
                by_sect[s] = cls()
            _accumulate(by_sect[s], res)

        overall.by_difficulty = by_diff
        overall.by_section = by_sect
        return overall

    # ------------------------------------------------------------------ display

    def summary_lines(self) -> list[str]:
        lines = [
            "─── Hierarchical Accuracy ───────────────────────────────",
            str(self.section),
            str(self.group),
            str(self.heading),
            str(self.subheading),
            str(self.code_8),
            str(self.code_10),
            f"{'score':12s}: {self.hierarchical_score:.4f}  (weighted composite)",
        ]
        return lines

    def by_difficulty_lines(self) -> list[str]:
        lines = ["─── Accuracy by Difficulty ──────────────────────────────"]
        for diff in Difficulty:
            m = self.by_difficulty.get(diff.value)
            if m:
                n = m.heading.total
                lines.append(
                    f"  {diff.value:8s}  heading={m.heading.accuracy:.3f}  "
                    f"code10={m.code_10.accuracy:.3f}  score={m.hierarchical_score:.4f}  (n={n})"
                )
        return lines

    def by_section_lines(self, top_n: int = 10) -> list[str]:
        lines = ["─── Accuracy by Section (heading level) ─────────────────"]
        sorted_sections = sorted(
            self.by_section.items(),
            key=lambda kv: kv[1].heading.accuracy,
        )
        for sect, m in sorted_sections[:top_n]:
            n = m.heading.total
            lines.append(
                f"  Section {sect:5s}  heading={m.heading.accuracy:.3f}  "
                f"code10={m.code_10.accuracy:.3f}  (n={n})"
            )
        return lines

    def error_analysis_lines(self, results: Sequence[CaseResult]) -> list[str]:
        """Where in the cascade is the first error made?"""
        level_order = ["section", "group", "heading", "subheading", "code_8", "code_10"]
        first_error_counts: dict[str | None, int] = defaultdict(int)

        for res in results:
            first_error_counts[res.first_error_level] += 1

        total = len(results)
        lines = ["─── First Error Level Distribution ──────────────────────"]
        lines.append(f"  {'correct (all levels)':22s}: {first_error_counts[None]}/{total}")
        for lvl in level_order:
            n = first_error_counts[lvl]
            lines.append(f"  {lvl:22s}: {n}/{total}  ({n/total*100:.1f}%)")
        return lines


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _accumulate(metrics: HierarchicalMetrics, res: CaseResult) -> None:
    """Add one CaseResult into a HierarchicalMetrics accumulator."""
    for lvl in LEVEL_WEIGHTS:
        stat: LevelStats = getattr(metrics, lvl)
        stat.total += 1
        if getattr(res, f"{lvl}_match"):
            stat.correct += 1
