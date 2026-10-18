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
        "--retry-case-id", action="append", default=[],
        help="Retry only this failed case (repeatable); preserve all other predictions",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-case timeout in seconds (default: 120). Increase for slow models.",
    )
    p.add_argument(
        "--cost-reference",
        type=Path,
        default=None,
        help="Trace with recorded API costs for the estimate "
             "(default: latest *_trace.jsonl under --results-dir)",
    )
    p.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        help="Refuse to start if the estimated cost exceeds this amount",
    )
    p.add_argument(
        "--skip-budget-check",
        action="store_true",
        help="Do not estimate the cost or query the OpenRouter balance before the run",
    )
    return p.parse_args()


def check_budget(args: argparse.Namespace, dataset) -> None:
    """Refuse to start a run that the balance or --max-cost-usd cannot cover."""
    from benchmark.evaluator.budget import estimate_cost, latest_trace, openrouter_remaining_usd
    from benchmark.schema.prediction import load_predictions
    from feap.config import settings

    pred_path = args.results_dir / f"{args.run_id}_predictions.jsonl"
    done = set()
    if not args.no_resume and pred_path.exists():
        done = {p.case_id for p in load_predictions(pred_path)
                if p.status != "error" or not args.retry_errors}
    n_cases = sum(c.id not in done for c in dataset.cases)

    reference = args.cost_reference or latest_trace(args.results_dir)
    estimate = estimate_cost(reference, n_cases) if reference else None
    if estimate is None:
        logger.warning("No recorded API costs to estimate the run: budget not checked")
        return
    remaining = asyncio.run(openrouter_remaining_usd(settings.OPENROUTER_API_KEY))
    logger.info(
        "Estimated cost $%.2f for %d cases (p90 $%.4f per case, %s); OpenRouter remaining: %s",
        estimate.estimate_usd, n_cases, estimate.per_case_p90_usd, estimate.reference,
        "unknown" if remaining is None else f"${remaining:.2f}",
    )
    if args.max_cost_usd is not None and estimate.estimate_usd > args.max_cost_usd:
        logger.error("Estimate exceeds --max-cost-usd %.2f", args.max_cost_usd)
        sys.exit(3)
    if remaining is not None and remaining < estimate.estimate_usd:
        logger.error("Balance does not cover the estimate: top up, run fewer cases "
                     "(--max) or pass --skip-budget-check")
        sys.exit(3)


async def run_classification(args: argparse.Namespace, dataset) -> tuple[list, list[str]]:
    """Run the cascade classifier on the dataset → (predictions, ids not run for budget)."""
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
        retry_case_ids=set(args.retry_case_id),
    )
    return predictions, runner.not_started


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
        if not args.skip_budget_check:
            check_budget(args, dataset)
        predictions, not_started = asyncio.run(run_classification(args, dataset))
        if not_started:
            # A report over the cases that happened to run would look like a result
            # (v32_refs); stop here and let resume finish the run.
            logger.error(
                "Run stopped by the OpenRouter budget: %d cases not run. Metrics are not "
                "computed for a partial run; add credits and rerun with the same --run-id.",
                len(not_started),
            )
            sys.exit(3)

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
