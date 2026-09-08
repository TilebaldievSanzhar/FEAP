"""Paired evaluation must retain failed cases and use the requested precision."""
from types import SimpleNamespace

import pytest

from benchmark.metrics.paired import compare
from benchmark.schema.prediction import CasePrediction
from benchmark.schema.test_case import Difficulty, GroundTruth


def dataset(n=1):
    return SimpleNamespace(cases=[SimpleNamespace(
        id=str(i), difficulty=Difficulty.EASY,
        ground_truth=GroundTruth.from_code10('8516400000', 'XVI'),
    ) for i in range(n)])


def prediction(cid='0', code='8516400000', status='completed'):
    return CasePrediction(case_id=cid, status=status, predicted_code_10=code,
                          predicted_confidence=1.0)


def test_missing_extra_and_duplicate_cases_are_rejected():
    for rows in ([], [prediction('extra')], [prediction(), prediction()]):
        with pytest.raises(ValueError):
            compare(dataset(), [prediction()], rows)


def test_failure_is_a_miss_even_with_stale_code_and_confidence():
    report = compare(dataset(), [prediction()], [prediction(status='error')])
    assert report['candidate']['accuracy']['code_10'] == 0
    assert report['candidate']['calibration']['code_10']['n_without_confidence'] == 1
    assert report['paired_code_10']['regressions'] == ['0']


def test_exact_paired_test_and_ten_digit_calibration():
    report = compare(dataset(6), [prediction(str(i), '8516400001') for i in range(6)],
                     [prediction(str(i)) for i in range(6)])
    assert report['baseline']['accuracy']['heading'] == 1
    assert report['baseline']['calibration']['heading']['ece'] == 0
    assert report['baseline']['calibration']['code_10']['ece'] == 1
    assert report['baseline']['code_10_brier'] == 1
    assert report['paired_code_10']['mcnemar_exact_two_sided_p'] == 0.03125
    assert report['paired_code_10']['accuracy_delta'] == 1


def test_identical_runs_have_no_discordance():
    report = compare(dataset(), [prediction()], [prediction()])
    assert report['paired_code_10']['mcnemar_exact_two_sided_p'] == 1
    assert report['paired_code_10']['accuracy_delta'] == 0
