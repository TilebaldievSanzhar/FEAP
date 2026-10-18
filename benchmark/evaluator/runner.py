"""BenchmarkRunner: runs the FEAP cascade classifier over a test dataset.

Features:
  - Async execution (one case at a time by default, configurable concurrency)
  - Incremental saving: results are written to JSONL after each case
    so a partial run can be resumed.
  - Resume support: already-evaluated case IDs are skipped.
  - Latency measurement per case.
  - Progress display.

Usage:
    runner = BenchmarkRunner(db_session_factory=get_db)
    predictions = await runner.run(dataset, output_path=Path("benchmark/data/results/run_001.jsonl"))
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from feap.cascade.orchestrator import CascadeOrchestrator
from benchmark.evaluator.budget import is_budget_error, retry_after_s
from benchmark.schema.prediction import CasePrediction, load_predictions, save_predictions
from benchmark.schema.test_case import TestCase, TestDataset

logger = logging.getLogger(__name__)

class BenchmarkRunner:
    """Runs the classifier on every test case in a dataset."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        concurrency: int = 1,
        case_timeout: float = 120.0,
        budget_retries: int = 3,
    ) -> None:
        """
        Args:
            session_factory: SQLAlchemy async_sessionmaker for DB access.
            concurrency:     Number of cases to run in parallel.
                             Keep at 1 to avoid hitting API rate limits.
            case_timeout:    Max seconds to wait for a single case before marking as error.
            budget_retries:  How many times to wait out an OpenRouter 402 before the
                             run stops starting new cases.
        """
        self.session_factory = session_factory
        self.concurrency = concurrency
        self.case_timeout = case_timeout
        self.budget_retries = budget_retries
        # Cases neither run nor recorded because the budget ran out: resume picks them up.
        self.not_started: list[str] = []
        self.budget_exhausted = False
        self._sleep = asyncio.sleep
        self._orchestrator = CascadeOrchestrator()

    # ------------------------------------------------------------------ public

    async def run(
        self,
        dataset: TestDataset,
        output_path: Path,
        resume: bool = True,
        retry_errors: bool = False,
        retry_case_ids: set[str] | None = None,
    ) -> list[CasePrediction]:
        """
        Classify every case in `dataset` and save predictions to `output_path`.

        Args:
            dataset:     The test dataset to evaluate.
            output_path: JSONL file to save predictions (appended to if resume=True).
            resume:      If True, skip cases already in output_path.

        Returns:
            List of all predictions (including previously saved ones if resumed).
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Load already-completed predictions (for resume)
        existing: list[CasePrediction] = []
        if resume and output_path.exists():
            existing = load_predictions(output_path)
            logger.info("Resuming: %d cases already done", len(existing))

        if retry_case_ids and (not resume or retry_errors):
            raise ValueError('Targeted retry requires resume and cannot combine with retry_errors')
        failed_ids = {p.case_id for p in existing if p.status == 'error'}
        if retry_case_ids and not retry_case_ids <= failed_ids:
            raise ValueError('Targeted retry IDs must refer to existing failed predictions')
        retry_ids = failed_ids if retry_errors else (retry_case_ids or set())
        if retry_errors or retry_case_ids:
            # Preserve the original failures, including infrastructure failures,
            # instead of silently replacing evidence with a later successful attempt.
            history_path = output_path.with_name(output_path.stem + '_retry_history.jsonl')
            with history_path.open('a', encoding='utf-8') as history:
                for pred in existing:
                    if pred.case_id in retry_ids:
                        history.write(pred.model_dump_json() + '\n')
            existing = [p for p in existing if p.case_id not in retry_ids]
            save_predictions(existing, output_path)

        done_ids = {p.case_id for p in existing}
        remaining = [c for c in dataset.cases if c.id not in done_ids]

        if not remaining:
            logger.info("All %d cases already evaluated.", len(dataset))
            return existing

        logger.info(
            "Running %d cases (skipping %d already done, concurrency=%d)",
            len(remaining),
            len(done_ids),
            self.concurrency,
        )

        new_predictions: list[CasePrediction] = []

        # Open output file in append mode
        with open(output_path, "a" if resume else "w", encoding="utf-8") as out_f:
            semaphore = asyncio.Semaphore(self.concurrency)

            async def run_one(case: TestCase, idx: int, total: int) -> CasePrediction | None:
                async with semaphore:
                    if self.budget_exhausted:
                        self.not_started.append(case.id)
                        return None
                    print(
                        f"[{idx+1:4d}/{total}] {case.id}  "
                        f"({case.difficulty.value})  heading={case.ground_truth.heading}",
                        end="  ... ",
                        flush=True,
                    )
                    pred = await self._classify_within_budget(case)
                    if pred is None:
                        self.not_started.append(case.id)
                        print("402  left for resume")
                        return None
                    status_icon = {"completed": "✓", "needs_input": "?", "error": "✗"}.get(
                        pred.status, "?"
                    )
                    print(f"{status_icon}  {pred.latency_ms:.0f}ms")

                    # Write immediately so partial runs are recoverable
                    out_f.write(pred.model_dump_json() + "\n")
                    out_f.flush()
                    return pred

            tasks = [run_one(case, idx, len(remaining)) for idx, case in enumerate(remaining)]
            new_predictions = [p for p in await asyncio.gather(*tasks) if p is not None]

        all_predictions = existing + new_predictions
        if self.not_started:
            logger.error(
                "OpenRouter budget exhausted: %d cases not run and not recorded; "
                "add credits and resume with the same run-id", len(self.not_started),
            )
        logger.info("Done. Total predictions: %d", len(all_predictions))
        return all_predictions

    # ------------------------------------------------------------------ private

    async def _classify_within_budget(self, case: TestCase) -> CasePrediction | None:
        """Wait out an OpenRouter 402; None once it persists (the run then stops)."""
        for attempt in range(self.budget_retries + 1):
            pred = await self._classify_case(case)
            if pred.status != "error" or not is_budget_error(pred.error_message):
                return pred
            if self.budget_exhausted or attempt == self.budget_retries:
                break
            wait = retry_after_s(pred.error_message)
            logger.warning("402 for %s, waiting %ds (attempt %d/%d)",
                           case.id, wait, attempt + 1, self.budget_retries)
            await self._sleep(wait)
        self.budget_exhausted = True
        return None

    async def _classify_case(self, case: TestCase) -> CasePrediction:
        """Run the cascade on one test case and return a CasePrediction."""
        start = time.perf_counter()
        response_dict: dict = {}

        try:
            async with self.session_factory() as session:
                response = await asyncio.wait_for(
                    self._orchestrator.classify(
                        description=case.product_description,
                        db=session,
                        additional_info=case.additional_info or None,
                    ),
                    timeout=self.case_timeout,
                )
                response_dict = response.model_dump()

        except asyncio.TimeoutError:
            logger.warning("Timeout for case %s", case.id)
            response_dict = {
                "session_id": "",
                "status": "error",
                "error": f"Timeout after {self.case_timeout}s",
            }

        except Exception as exc:
            logger.exception("Error classifying case %s", case.id)
            response_dict = {
                "session_id": "",
                "status": "error",
                "error": str(exc),
            }

        latency_ms = (time.perf_counter() - start) * 1000

        return CasePrediction.from_response(
            case_id=case.id,
            response_dict=response_dict,
            latency_ms=latency_ms,
        )
