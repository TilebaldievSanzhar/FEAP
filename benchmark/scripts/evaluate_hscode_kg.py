"""Evaluate benchmark against hscode.customs.gov.kg external classifier.

Sends each test case to the Kyrgyz customs AI classifier and converts
their response into our CasePrediction format for comparison.

Rate-limited by default (1 request per 5 seconds) to avoid overloading
the external service.

Usage:
    python benchmark/scripts/evaluate_hscode_kg.py \
        --dataset benchmark/data/cases/benchmark_100.jsonl \
        --run-id hscode_kg_v1

    # Metrics only (from saved predictions):
    python benchmark/scripts/evaluate_hscode_kg.py \
        --dataset benchmark/data/cases/benchmark_100.jsonl \
        --run-id hscode_kg_v1 \
        --metrics-only --predictions benchmark/data/results/hscode_kg/hscode_kg_v1_predictions.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parents[2]))

import httpx

from benchmark.schema.case_v2 import load_dataset
from benchmark.schema.test_case import TestCase, TestDataset
from benchmark.schema.prediction import CasePrediction, load_predictions

# --- hscode.customs.gov.kg API config ---

HSCODE_KG_API_URL = "https://hscode.customs.gov.kg/api/search"
HSCODE_KG_MODEL = "model_v5_2v"

# ТН ВЭД group (2-digit) → section (Roman numeral) mapping
# Standard HS/EAEU nomenclature, static
GROUP_TO_SECTION = {}
_SECTION_RANGES = [
    ("I", range(1, 6)),       # 01-05
    ("II", range(6, 15)),     # 06-14
    ("III", [15]),
    ("IV", range(16, 25)),    # 16-24
    ("V", range(25, 28)),     # 25-27
    ("VI", range(28, 39)),    # 28-38
    ("VII", range(39, 41)),   # 39-40
    ("VIII", range(41, 44)),  # 41-43
    ("IX", range(44, 47)),    # 44-46
    ("X", range(47, 50)),     # 47-49
    ("XI", range(50, 64)),    # 50-63
    ("XII", range(64, 68)),   # 64-67
    ("XIII", range(68, 71)),  # 68-70
    ("XIV", [71]),
    ("XV", range(72, 84)),    # 72-83
    ("XVI", range(84, 86)),   # 84-85
    ("XVII", range(86, 90)),  # 86-89
    ("XVIII", range(90, 93)), # 90-92
    ("XIX", [93]),
    ("XX", range(94, 97)),    # 94-96
    ("XXI", [97]),
]
for _sec, _groups in _SECTION_RANGES:
    for _g in _groups:
        GROUP_TO_SECTION[f"{_g:02d}"] = _sec

# Headers mimicking a browser session (required: same-origin CORS policy)
HSCODE_KG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Origin": "https://hscode.customs.gov.kg",
    "Referer": "https://hscode.customs.gov.kg/",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run FEAP benchmark against hscode.customs.gov.kg"
    )
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--run-id", type=str, default="hscode_kg_v1")
    p.add_argument(
        "--results-dir",
        type=Path,
        default=Path("benchmark/data/results/hscode_kg"),
    )
    p.add_argument("--max", type=int, default=None, help="Limit number of cases")
    p.add_argument(
        "--delay",
        type=float,
        default=5.0,
        help="Delay between requests in seconds (default: 5)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Per-request timeout in seconds",
    )
    p.add_argument("--metrics-only", action="store_true")
    p.add_argument("--predictions", type=Path, default=None)
    p.add_argument("--no-resume", action="store_true")
    return p.parse_args()


def parse_hscode_kg_response(
    case_id: str,
    data: dict,
    latency_ms: float,
) -> CasePrediction:
    """Convert hscode.customs.gov.kg response to our CasePrediction format.

    Their response:
    {
        "search_id": 329,
        "cascade": "87 → 8716 → 871610",
        "candidates": [
            {"code": "8716395001", "description": "...", "score": 1.07,
             "p2": 0.71, "p4": 0.95, "p6": 0.07, "p10": 0.98, "chain": "..."},
            ...
        ],
        "confidence_status": "✅ Strong match."
    }
    """
    candidates = data.get("candidates", [])

    if not candidates:
        return CasePrediction(
            case_id=case_id,
            status="error",
            error_message="No candidates returned",
            latency_ms=latency_ms,
            ran_at=datetime.now(UTC).isoformat(),
            raw_response=data,
        )

    # Top-1 candidate is their prediction
    top = candidates[0]
    code = top.get("code", "").replace(" ", "")

    pred = CasePrediction(
        case_id=case_id,
        status="completed",
        latency_ms=latency_ms,
        ran_at=datetime.now(UTC).isoformat(),
        raw_response=data,
    )

    if len(code) == 10 and code.isdigit():
        pred.predicted_code_10 = code
        pred.predicted_code_8 = code[:8]
        pred.predicted_subheading = code[:6]
        pred.predicted_heading = code[:4]
        pred.predicted_group = code[:2]
        pred.predicted_section = GROUP_TO_SECTION.get(code[:2])

    # Use p10 as confidence (probability at 10-digit level)
    pred.predicted_confidence = top.get("p10")

    return pred


def classify_via_hscode_kg(
    client: httpx.Client,
    case: TestCase,
    timeout: float,
) -> CasePrediction:
    """Send one case to hscode.customs.gov.kg and return a CasePrediction."""
    start = time.perf_counter()

    try:
        payload = {
            "description": case.product_description,
            "model": HSCODE_KG_MODEL,
        }

        resp = client.post(
            HSCODE_KG_API_URL,
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()

    except httpx.TimeoutException:
        latency_ms = (time.perf_counter() - start) * 1000
        return CasePrediction(
            case_id=case.id,
            status="error",
            error_message=f"Timeout after {timeout}s",
            latency_ms=latency_ms,
            ran_at=datetime.now(UTC).isoformat(),
            raw_response={},
        )
    except httpx.HTTPStatusError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return CasePrediction(
            case_id=case.id,
            status="error",
            error_message=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            latency_ms=latency_ms,
            ran_at=datetime.now(UTC).isoformat(),
            raw_response={},
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return CasePrediction(
            case_id=case.id,
            status="error",
            error_message=str(exc),
            latency_ms=latency_ms,
            ran_at=datetime.now(UTC).isoformat(),
            raw_response={},
        )

    latency_ms = (time.perf_counter() - start) * 1000
    return parse_hscode_kg_response(case.id, data, latency_ms)


def run_classification(
    args: argparse.Namespace, dataset: TestDataset
) -> list[CasePrediction]:
    pred_path = args.results_dir / f"{args.run_id}_predictions.jsonl"
    pred_path.parent.mkdir(parents=True, exist_ok=True)

    # Resume support
    existing: list[CasePrediction] = []
    if not args.no_resume and pred_path.exists():
        existing = load_predictions(pred_path)
        print(f"Resuming: {len(existing)} cases already done")

    done_ids = {p.case_id for p in existing}
    remaining = [c for c in dataset.cases if c.id not in done_ids]

    if not remaining:
        print(f"All {len(dataset)} cases already evaluated.")
        return existing

    total_time_est = len(remaining) * args.delay
    print(f"Running {len(remaining)} cases (skipping {len(done_ids)} already done)")
    print(f"Estimated time: ~{total_time_est/60:.1f} min (delay={args.delay}s per request)")
    print()

    new_predictions: list[CasePrediction] = []

    with httpx.Client(headers=HSCODE_KG_HEADERS) as client:
        with open(pred_path, "a", encoding="utf-8") as out_f:
            for idx, case in enumerate(remaining):
                print(
                    f"[{idx+1:3d}/{len(remaining)}] {case.id}  "
                    f"({case.difficulty.value})  heading={case.ground_truth.heading}",
                    end="  ... ",
                    flush=True,
                )

                pred = classify_via_hscode_kg(client, case, args.timeout)

                status_icon = {
                    "completed": "OK",
                    "needs_input": "??",
                    "error": "ERR",
                }.get(pred.status, "?")

                code_display = pred.predicted_code_10 or "-"
                conf_display = (
                    f"{pred.predicted_confidence:.2f}"
                    if pred.predicted_confidence is not None
                    else "-"
                )
                print(
                    f"{status_icon}  {pred.latency_ms:.0f}ms  "
                    f"code={code_display}  conf={conf_display}"
                )

                out_f.write(pred.model_dump_json() + "\n")
                out_f.flush()
                new_predictions.append(pred)

                # Rate limiting — wait before next request
                if idx < len(remaining) - 1:
                    time.sleep(args.delay)

    all_predictions = existing + new_predictions
    print(f"\nDone. Total predictions: {len(all_predictions)}")
    return all_predictions


def main() -> None:
    args = parse_args()

    # Load dataset (v1 or v2 cases)
    dataset = load_dataset(args.dataset)
    if args.max:
        dataset = TestDataset(name=dataset.name, cases=dataset.cases[: args.max])
    print(dataset.summary())
    print()

    # Load or generate predictions
    if args.metrics_only:
        if args.predictions is None:
            # Try default path
            default_pred = args.results_dir / f"{args.run_id}_predictions.jsonl"
            if default_pred.exists():
                args.predictions = default_pred
            else:
                print("ERROR: --metrics-only requires --predictions or existing predictions file")
                sys.exit(1)
        predictions = load_predictions(args.predictions)
        print(f"Loaded {len(predictions)} predictions from {args.predictions}")
    else:
        predictions = run_classification(args, dataset)

    # Align predictions with cases
    pred_by_id = {p.case_id: p for p in predictions}
    cases_aligned = []
    preds_aligned = []

    for case in dataset.cases:
        pred = pred_by_id.get(case.id)
        if pred is None:
            print(f"WARNING: No prediction for case {case.id} -- skipping")
            continue
        cases_aligned.append(case)
        preds_aligned.append(pred)

    if not cases_aligned:
        print("ERROR: No aligned (case, prediction) pairs found.")
        sys.exit(1)

    # Compare
    from benchmark.evaluator.comparator import Comparator

    comparator = Comparator()
    results = comparator.compare_all(cases_aligned, preds_aligned)

    # Build report
    from benchmark.metrics.report import BenchmarkReport

    report = BenchmarkReport.build(
        name=f"{dataset.name} / {args.run_id} (hscode.customs.gov.kg)",
        cases=cases_aligned,
        results=results,
    )

    # Print
    report.print()
    lines = report.hierarchical.error_analysis_lines(results)
    print("\n".join(lines))

    # Save
    args.results_dir.mkdir(parents=True, exist_ok=True)
    report.save_text(args.results_dir / f"{args.run_id}_report.txt")
    report.save_json(args.results_dir / f"{args.run_id}_report.json")

    # Save per-case results
    from dataclasses import asdict

    results_path = args.results_dir / f"{args.run_id}_case_results.jsonl"
    with open(results_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    print(f"Per-case results saved to {results_path}")


if __name__ == "__main__":
    main()
