"""Budget guard: a 402 must not turn the rest of a run into recorded failures."""
import json
from types import SimpleNamespace

from benchmark.evaluator.budget import estimate_cost, is_budget_error, retry_after_s
from benchmark.evaluator.runner import BenchmarkRunner
from benchmark.schema.prediction import CasePrediction, load_predictions

IN_FLIGHT_402 = (
    "Error code: 402 - {'error': {'message': 'This request would exceed your available "
    "credits given your current in-flight requests.', 'code': 402, 'metadata': {'reason': "
    "'in_flight_budget_exhausted', 'headers': {'Retry-After': '120'}}}}"
)


def dataset(n=3):
    return SimpleNamespace(cases=[SimpleNamespace(
        id=f'case{i}', difficulty=SimpleNamespace(value='easy'),
        ground_truth=SimpleNamespace(heading='8516'),
    ) for i in range(n)])


class Runner(BenchmarkRunner):
    """Answers from a script; records sleeps instead of waiting."""

    def __init__(self, script, **kwargs):
        super().__init__(None, **kwargs)
        self.script = list(script)
        self.slept = []

        async def sleep(seconds):
            self.slept.append(seconds)
        self._sleep = sleep

    async def _classify_case(self, case):
        error = self.script.pop(0) if self.script else None
        if error:
            return CasePrediction(case_id=case.id, status='error', error_message=error)
        return CasePrediction(case_id=case.id, status='completed')


def test_detects_openrouter_402_and_retry_after():
    assert is_budget_error(IN_FLIGHT_402)
    assert not is_budget_error('Timeout after 300s') and not is_budget_error(None)
    assert retry_after_s(IN_FLIGHT_402) == 120
    assert retry_after_s('no header', default=30) == 30


async def test_in_flight_402_is_waited_out_and_the_case_recorded(tmp_path):
    path = tmp_path / 'predictions.jsonl'
    runner = Runner([IN_FLIGHT_402, None, None, None])
    await runner.run(dataset(), path, resume=False)
    assert runner.slept == [120] and runner.not_started == []
    assert [p.status for p in load_predictions(path)] == ['completed'] * 3


async def test_persistent_402_stops_the_run_without_recording_failures(tmp_path):
    path = tmp_path / 'predictions.jsonl'
    runner = Runner([None] + [IN_FLIGHT_402] * 10, budget_retries=2)
    predictions = await runner.run(dataset(), path, resume=False)
    assert runner.budget_exhausted
    assert runner.not_started == ['case1', 'case2']
    assert [p.case_id for p in predictions] == ['case0']
    assert [p.case_id for p in load_predictions(path)] == ['case0']


def test_estimate_uses_p90_cost_per_case(tmp_path):
    trace = tmp_path / 'run_trace.jsonl'
    rows = [{'case_id': f'c{i}', 'usage': {'cost': 0.01 * (i + 1)}} for i in range(10)]
    rows.append({'case_id': 'c9', 'usage': {'cost': 0.05}})  # second call of the same case
    trace.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
    estimate = estimate_cost(trace, n_cases=100, safety=1.5)
    assert estimate.per_case_p90_usd == 0.09
    assert estimate.estimate_usd == 13.5
    empty = tmp_path / 'empty_trace.jsonl'
    empty.write_text(json.dumps({'case_id': 'c', 'usage': None}), encoding='utf-8')
    assert estimate_cost(empty, n_cases=5) is None
