"""Benchmark reruns must not silently retain failed or duplicate predictions."""
from types import SimpleNamespace

import pytest

from benchmark.evaluator.runner import BenchmarkRunner
from benchmark.schema.prediction import CasePrediction, load_predictions, save_predictions


class Runner(BenchmarkRunner):
    async def _classify_case(self, case):
        return CasePrediction(case_id=case.id, status='completed')


def dataset():
    return SimpleNamespace(cases=[SimpleNamespace(
        id='case', difficulty=SimpleNamespace(value='easy'),
        ground_truth=SimpleNamespace(heading='8516'),
    )])


async def test_no_resume_replaces_old_predictions(tmp_path):
    path = tmp_path / 'predictions.jsonl'
    save_predictions([CasePrediction(case_id='case', status='error')], path)
    await Runner(None).run(dataset(), path, resume=False)
    predictions = load_predictions(path)
    assert len(predictions) == 1
    assert predictions[0].status == 'completed'


async def test_retry_errors_keeps_completed_cases_and_replaces_failures(tmp_path):
    path = tmp_path / 'predictions.jsonl'
    save_predictions([
        CasePrediction(case_id='old', status='completed'),
        CasePrediction(case_id='case', status='error'),
    ], path)
    await Runner(None).run(dataset(), path, retry_errors=True)
    predictions = load_predictions(path)
    assert [p.case_id for p in predictions] == ['old', 'case']
    assert all(p.status == 'completed' for p in predictions)


async def test_targeted_retry_preserves_other_failures_and_archives_original(tmp_path):
    path = tmp_path / 'predictions.jsonl'
    original = CasePrediction(case_id='case', status='error', error_message='Connection closed')
    save_predictions([
        original, CasePrediction(case_id='model_error', status='error', error_message='Bad choice'),
    ], path)
    await Runner(None).run(dataset(), path, retry_case_ids={'case'})
    rows = {p.case_id: p for p in load_predictions(path)}
    assert rows['case'].status == 'completed'
    assert rows['model_error'].status == 'error'
    assert load_predictions(tmp_path / 'predictions_retry_history.jsonl') == [original]


async def test_targeted_retry_cannot_reclassify_a_completed_case(tmp_path):
    path = tmp_path / 'predictions.jsonl'
    save_predictions([CasePrediction(case_id='case', status='completed')], path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='failed predictions'):
        await Runner(None).run(dataset(), path, retry_case_ids={'case'})
    assert path.read_bytes() == before
