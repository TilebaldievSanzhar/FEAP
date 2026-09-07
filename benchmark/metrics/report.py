"""Aggregate benchmark report builder.

Combines HierarchicalMetrics + CalibrationMetrics + FinancialMetrics
into a single printable and JSON-serialisable report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from benchmark.metrics.calibration import CalibrationMetrics
from benchmark.metrics.financial import FinancialMetrics
from benchmark.metrics.hierarchical import HierarchicalMetrics
from benchmark.schema.prediction import CaseResult
from benchmark.schema.test_case import Difficulty, TestCase


@dataclass
class SystemBehaviourStats:
    """How did the system behave — did it complete, stop, error?"""

    total: int = 0
    completed: int = 0
    needs_input: int = 0   # system asked for clarification
    error: int = 0

    # Scenario alignment
    correctly_stopped: int = 0   # required clarification AND system stopped
    wrongly_stopped: int = 0     # did NOT need clarification but system stopped

    trap_hits: int = 0           # predicted code is one of the known trap_codes

    @property
    def completion_rate(self) -> float:
        return self.completed / self.total if self.total else 0.0

    @property
    def clarification_precision(self) -> float:
        """Of the times the system stopped, how often was it right to?"""
        stopped = self.needs_input
        return self.correctly_stopped / stopped if stopped else 0.0

    def summary_lines(self) -> list[str]:
        return [
            "─── System Behaviour ────────────────────────────────────",
            f"  Total cases        : {self.total}",
            f"  Completed          : {self.completed}  ({self.completion_rate:.1%})",
            f"  Needs input        : {self.needs_input}",
            f"  Errors             : {self.error}",
            f"  Correctly stopped  : {self.correctly_stopped}",
            f"  Wrongly stopped    : {self.wrongly_stopped}",
            f"  Fell into trap code: {self.trap_hits}",
        ]


@dataclass
class BenchmarkReport:
    """Full benchmark report."""

    name: str
    ran_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    n_cases: int = 0

    # Sub-reports
    hierarchical: HierarchicalMetrics = field(default_factory=HierarchicalMetrics)
    calibration: CalibrationMetrics = field(default_factory=CalibrationMetrics)
    financial: FinancialMetrics = field(default_factory=FinancialMetrics)
    behaviour: SystemBehaviourStats = field(default_factory=SystemBehaviourStats)

    # Latency
    mean_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0

    @classmethod
    def build(
        cls,
        name: str,
        cases: Sequence[TestCase],
        results: Sequence[CaseResult],
    ) -> "BenchmarkReport":
        assert len(cases) == len(results)

        report = cls(name=name, n_cases=len(cases))

        # ── hierarchical ─────────────────────────────────────────────
        report.hierarchical = HierarchicalMetrics.compute(cases, results)

        # ── calibration ───────────────────────────────────────────────
        report.calibration = CalibrationMetrics.compute(results)

        # ── financial ─────────────────────────────────────────────────
        report.financial = FinancialMetrics.compute(cases, results)

        # ── behaviour ─────────────────────────────────────────────────
        beh = SystemBehaviourStats(total=len(results))
        for case, res in zip(cases, results, strict=True):
            if res.status == "completed":
                beh.completed += 1
            elif res.status == "needs_input":
                beh.needs_input += 1
            else:
                beh.error += 1

            if res.correctly_stopped:
                beh.correctly_stopped += 1
            if res.wrongly_stopped:
                beh.wrongly_stopped += 1
            if res.fell_into_trap:
                beh.trap_hits += 1

        report.behaviour = beh

        # ── latency ───────────────────────────────────────────────────
        latencies = sorted(r.latency_ms for r in results)
        if latencies:
            report.mean_latency_ms = sum(latencies) / len(latencies)
            report.p50_latency_ms = _percentile(latencies, 50)
            report.p95_latency_ms = _percentile(latencies, 95)
            report.p99_latency_ms = _percentile(latencies, 99)

        return report

    # ------------------------------------------------------------------ display

    def print(self) -> None:
        lines = self._all_lines()
        print("\n".join(lines))

    def _all_lines(self) -> list[str]:
        h = self.hierarchical
        lines = [
            "",
            f"╔══ Benchmark Report: {self.name} ══╗",
            f"  Run at : {self.ran_at}",
            f"  Cases  : {self.n_cases}",
            "",
        ]

        # ── Key metrics table ─────────────────────────────────────────
        lines += [
            "─── Key Metrics ─────────────────────────────────────────",
            f"  {'Heading accuracy (4-digit)':32s}: {h.heading_accuracy:.3f}  ★ primary KPI",
            f"  {'Subheading accuracy (6-digit)':32s}: {h.subheading_accuracy:.3f}",
            f"  {'Code-10 accuracy (10-digit)':32s}: {h.code_10_accuracy:.3f}",
            f"  {'Hierarchical score (weighted)':32s}: {h.hierarchical_score:.4f}",
            f"  {'ECE (calibration)':32s}: {self.calibration.ece:.4f}",
            f"  {'Duty rate accuracy':32s}: {self.financial.duty_accuracy:.3f}",
            "",
        ]

        lines += self.behaviour.summary_lines()
        lines.append("")
        lines += h.summary_lines()
        lines.append("")
        lines += h.by_difficulty_lines()
        lines.append("")
        lines += h.by_section_lines()
        lines.append("")
        lines += self.calibration.summary_lines()
        lines.append("")
        lines += self.financial.summary_lines()
        lines += [
            "",
            "─── Latency ─────────────────────────────────────────────",
            f"  mean : {self.mean_latency_ms:.0f} ms",
            f"  p50  : {self.p50_latency_ms:.0f} ms",
            f"  p95  : {self.p95_latency_ms:.0f} ms",
            f"  p99  : {self.p99_latency_ms:.0f} ms",
            "",
            "╚" + "═" * 55 + "╝",
        ]
        return lines

    # ------------------------------------------------------------------ IO

    def save_text(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self._all_lines()), encoding="utf-8")
        print(f"Report saved to {path}")

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "name": self.name,
            "ran_at": self.ran_at,
            "n_cases": self.n_cases,
            "key_metrics": {
                "heading_accuracy": self.hierarchical.heading_accuracy,
                "subheading_accuracy": self.hierarchical.subheading_accuracy,
                "code_10_accuracy": self.hierarchical.code_10_accuracy,
                "hierarchical_score": self.hierarchical.hierarchical_score,
                "ece": self.calibration.ece,
                "duty_accuracy": self.financial.duty_accuracy,
            },
            "behaviour": {
                "completion_rate": self.behaviour.completion_rate,
                "needs_input": self.behaviour.needs_input,
                "errors": self.behaviour.error,
                "trap_hits": self.behaviour.trap_hits,
            },
            "latency_ms": {
                "mean": self.mean_latency_ms,
                "p50": self.p50_latency_ms,
                "p95": self.p95_latency_ms,
                "p99": self.p99_latency_ms,
            },
            "by_difficulty": {
                d.value: {
                    "heading_accuracy": m.heading_accuracy,
                    "code_10_accuracy": m.code_10_accuracy,
                    "n": m.heading.total,
                }
                for d in Difficulty
                if (m := self.hierarchical.by_difficulty.get(d.value))
            },
            "by_section": {
                sect: {
                    "heading_accuracy": m.heading_accuracy,
                    "code_10_accuracy": m.code_10_accuracy,
                    "n": m.heading.total,
                }
                for sect, m in self.hierarchical.by_section.items()
            },
            "reliability_diagram": self.calibration.reliability_diagram_data(),
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON report saved to {path}")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _percentile(sorted_values: list[float], pct: int) -> float:
    if not sorted_values:
        return 0.0
    idx = int(len(sorted_values) * pct / 100)
    idx = min(idx, len(sorted_values) - 1)
    return sorted_values[idx]
