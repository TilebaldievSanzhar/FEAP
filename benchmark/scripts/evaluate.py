"""Script: run the full benchmark evaluation pipeline.

Steps:
  1. Load test dataset (JSONL)
  2. Run the classifier on each case (or load existing predictions)
  3. Compare predictions vs ground truth
  4. Compute all metrics
  5. Print and save report

Run (full evaluation):
    python -m benchmark.scripts.evaluate \\
        --dataset benchmark/data/cases/benchmark_v1.jsonl \\
        --run-id v1

Run (metrics only, from existing predictions):
    python -m benchmark.scripts.evaluate \\
        --dataset benchmark/data/cases/benchmark_v1.jsonl \\
        --predictions benchmark/data/results/v1_predictions.jsonl \\
        --metrics-only

Run (quick smoke test, 5 cases):
    python -m benchmark.scripts.evaluate \\
        --dataset benchmark/data/cases/benchmark_v1.jsonl \\
        --max 5 --run-id smoke
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run FEAP benchmark evaluation")
    p.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="Path to benchmark JSONL dataset",
    )
    p.add_argument(
        "--run-id",
        type=str,
        default="run",
        help="Identifier for this run (used in output filenames)",
    )
    p.add_argument(
        "--predictions",
        type=Path,
        default=None,
        help="Path to existing predictions JSONL (skip classification step)",
    )
    p.add_argument(
        "--results-dir",
        type=Path,
        default=Path("benchmark/data/results"),
        help="Directory to save predictions and reports",
    )
    p.add_argument(
        "--max",
        type=int,
        default=None,
        help="Limit to first N cases (for quick tests)",
    )
    p.add_argument(
        "--metrics-only",
        action="store_true",
        help="Skip classification; compute metrics from --predictions file",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of parallel classification requests",
    )
    p.add_argument(
        "--no-resume",
        action="store_true",
        help="Start fresh, ignoring any existing predictions file",
    )
    p.add_argument(
        "--retry-errors",
        action="store_true",
        help="On resume, retry failed requests and keep completed predictions",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-case timeout in seconds (default: 120). Increase for slow models.",
    )
    return p.parse_args()


async def run_classification(args: argparse.Namespace, dataset) -> list:
    """Run the cascade classifier on the dataset."""
    from feap.db.engine import async_session
    from benchmark.evaluator.runner import BenchmarkRunner

    runner = BenchmarkRunner(
        session_factory=async_session,
        concurrency=args.concurrency,
        case_timeout=args.timeout,
    )

    pred_path = args.results_dir / f"{args.run_id}_predictions.jsonl"
    predictions = await runner.run(
        dataset=dataset,
        output_path=pred_path,
        resume=not args.no_resume,
        retry_errors=args.retry_errors,
    )
    return predictions


def main() -> None:
    args = parse_args()

    # ── Load dataset ──────────────────────────────────────────────────
    from benchmark.schema.test_case import TestDataset

    dataset = TestDataset.load_jsonl(args.dataset)
    if args.max:
        dataset = TestDataset(
            name=dataset.name,
            cases=dataset.cases[: args.max],
        )
    print(dataset.summary())
    print()

    # ── Load or generate predictions ──────────────────────────────────
    from benchmark.schema.prediction import load_predictions

    if args.metrics_only:
        if args.predictions is None:
            logger.error("--metrics-only requires --predictions")
            sys.exit(1)
        predictions = load_predictions(args.predictions)
        logger.info("Loaded %d predictions from %s", len(predictions), args.predictions)
    else:
        predictions = asyncio.run(run_classification(args, dataset))

    # ── Align predictions with cases ──────────────────────────────────
    # Build a lookup by case_id (in case order differs after resume)
    pred_by_id = {p.case_id: p for p in predictions}
    cases_aligned = []
    preds_aligned = []

    for case in dataset.cases:
        pred = pred_by_id.get(case.id)
        if pred is None:
            logger.warning("No prediction for case %s — skipping", case.id)
            continue
        cases_aligned.append(case)
        preds_aligned.append(pred)

    if not cases_aligned:
        logger.error("No aligned (case, prediction) pairs found.")
        sys.exit(1)

    # ── Compare ───────────────────────────────────────────────────────
    from benchmark.evaluator.comparator import Comparator

    comparator = Comparator()
    results = comparator.compare_all(cases_aligned, preds_aligned)

    # ── Build report ──────────────────────────────────────────────────
    from benchmark.metrics.report import BenchmarkReport

    report = BenchmarkReport.build(
        name=f"{dataset.name} / {args.run_id}",
        cases=cases_aligned,
        results=results,
    )

    # ── Print ─────────────────────────────────────────────────────────
    report.print()

    # Also print the error analysis (first error level distribution)
    lines = report.hierarchical.error_analysis_lines(results)
    print("\n".join(lines))

    # ── Save ──────────────────────────────────────────────────────────
    args.results_dir.mkdir(parents=True, exist_ok=True)
    report.save_text(args.results_dir / f"{args.run_id}_report.txt")
    report.save_json(args.results_dir / f"{args.run_id}_report.json")

    # Save per-case results as JSONL for further analysis
    import json
    from dataclasses import asdict

    results_path = args.results_dir / f"{args.run_id}_case_results.jsonl"
    with open(results_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    logger.info("Per-case results saved to %s", results_path)


if __name__ == "__main__":
    main()
