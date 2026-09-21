"""Strict paired comparisons with ten-digit accuracy as the primary outcome."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict
from statistics import mean, median

from benchmark.metrics.calibration import CalibrationMetrics
from benchmark.schema.prediction import CasePrediction, CaseResult
from benchmark.schema.test_case import TestDataset


def _index(predictions: list[CasePrediction], expected: set[str]):
    rows = {p.case_id: p for p in predictions}
    if len(rows) != len(predictions):
        raise ValueError('Duplicate case IDs; resolve repeated predictions before comparison')
    if set(rows) != expected:
        raise ValueError(
            f'Case coverage mismatch: missing={sorted(expected - set(rows))}, '
            f'extra={sorted(set(rows) - expected)}'
        )
    return rows


def _score(cases, predictions):
    results = []
    for case in cases:
        pred = predictions[case.id]
        code = pred.predicted_code_10 if pred.status == 'completed' else None
        valid = bool(code and len(code) == 10 and code.isascii() and code.isdigit())
        results.append(CaseResult(
            case_id=case.id, difficulty=case.difficulty.value,
            section=case.ground_truth.section, status=pred.status,
            heading_match=valid and code[:4] == case.ground_truth.heading,
            subheading_match=valid and code[:6] == case.ground_truth.subheading,
            code_8_match=valid and code[:8] == case.ground_truth.code_8,
            code_10_match=valid and code == case.ground_truth.code_10,
            confidence=pred.predicted_confidence if valid else None,
            latency_ms=pred.latency_ms,
        ))
    return results


def _summary(results):
    n = len(results)
    counts = {name: sum(getattr(r, name + '_match') for r in results)
              for name in ('heading', 'subheading', 'code_8', 'code_10')}
    confident = [r for r in results if r.confidence is not None]
    calibration = {}
    for name, heading in (('heading', True), ('code_10', False)):
        metric = CalibrationMetrics.compute(results, use_heading=heading)
        calibration[name] = asdict(metric)
    return {
        'n': n, 'status': dict(Counter(r.status for r in results)),
        'coverage': sum(r.status == 'completed' for r in results) / n,
        'accuracy_on_completed': counts['code_10'] / sum(r.status == 'completed' for r in results)
        if any(r.status == 'completed' for r in results) else None,
        'high_confidence_errors': sum(r.confidence is not None and r.confidence >= 0.8
                                      and not r.code_10_match for r in results),
        'correct': counts, 'accuracy': {k: v / n for k, v in counts.items()},
        'l4_given_correct_heading': {
            'correct': counts['subheading'], 'n': counts['heading'],
            'accuracy': counts['subheading'] / counts['heading'] if counts['heading'] else None,
        },
        'code_10_given_correct_subheading': {
            'correct': counts['code_10'], 'n': counts['subheading'],
            'accuracy': counts['code_10'] / counts['subheading'] if counts['subheading'] else None,
        },
        'calibration': calibration,
        'code_10_brier': mean((r.confidence - r.code_10_match) ** 2 for r in confident)
        if confident else None,
        'latency_ms': {'mean': mean(r.latency_ms for r in results),
                       'median': median(r.latency_ms for r in results)},
    }


def compare(dataset: TestDataset, baseline: list[CasePrediction], candidate: list[CasePrediction]):
    """Count errors/abstentions as misses, requiring both complete case-ID sets.

    The exact two-sided McNemar test uses discordant pairs, not independent
    proportions. It describes this fixed dataset and does not correct label errors
    or establish generalisation to unseen goods.
    """
    cases = sorted(dataset.cases, key=lambda c: c.id)
    expected = {c.id for c in cases}
    if not cases or len(expected) != len(cases):
        raise ValueError('Dataset must be non-empty with unique case IDs')
    left = _index(baseline, expected)
    right = _index(candidate, expected)
    left_results, right_results = _score(cases, left), _score(cases, right)
    wins, regressions, changes = [], [], []
    for case, a, b in zip(cases, left_results, right_results, strict=True):
        if b.code_10_match and not a.code_10_match:
            wins.append(case.id)
        if a.code_10_match and not b.code_10_match:
            regressions.append(case.id)
        if left[case.id].predicted_code_10 != right[case.id].predicted_code_10:
            changes.append({
                'case_id': case.id, 'expected': case.ground_truth.code_10,
                'baseline': left[case.id].predicted_code_10,
                'candidate': right[case.id].predicted_code_10,
                'baseline_correct': a.code_10_match, 'candidate_correct': b.code_10_match,
            })
    discordant = len(wins) + len(regressions)
    p_value = min(1.0, 2 * sum(math.comb(discordant, k)
                              for k in range(min(len(wins), len(regressions)) + 1))
                  / 2 ** discordant)
    return {
        'baseline': _summary(left_results), 'candidate': _summary(right_results),
        'by_difficulty': {
            difficulty: {
                'baseline': _summary([r for r in left_results if r.difficulty == difficulty]),
                'candidate': _summary([r for r in right_results if r.difficulty == difficulty]),
            }
            for difficulty in sorted({r.difficulty for r in left_results})
        },
        'paired_code_10': {
            'wins': wins, 'regressions': regressions, 'discordant_pairs': discordant,
            'accuracy_delta': (len(wins) - len(regressions)) / len(cases),
            'mcnemar_exact_two_sided_p': p_value,
        },
        'changed_predictions': changes,
    }
