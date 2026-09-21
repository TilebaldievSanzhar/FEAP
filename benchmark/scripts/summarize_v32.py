"""Reproduce v32 results without disguising partial runs or relabeling as gains."""
import hashlib
import json
from pathlib import Path

from benchmark.metrics.paired import compare
from benchmark.schema.prediction import load_predictions
from benchmark.schema.test_case import TestDataset

ROOT = Path('benchmark/data/results')
CASES = Path('benchmark/data/cases')
RUNS = {
    'v32_baseline': 'benchmark_100',
    'v32_control_baseline': 'v32_control16',
    'v32_dev_evidence': 'v32_dev8',
    'v32_evidence': 'benchmark_100',
    'v32_control_evidence': 'v32_control16',
    'v32_clarify': 'v32_dev8',
    'v32_refs': 'benchmark_100',
    'v32_refs_clarify': 'v32_dev8',
    'v32_final_smoke': 'v32_final_smoke4',
}


def read(run):
    return load_predictions(ROOT / run / (run + '_predictions.jsonl'))


def subset(dataset, ids):
    return TestDataset(dataset.name, [c for c in dataset if c.id in ids])


def main():
    report = {'runs': {}}
    for run, data in RUNS.items():
        path = ROOT / run / (run + '_predictions.jsonl')
        if not path.exists():
            continue
        rows = read(run)
        dataset = TestDataset.load_jsonl(CASES / (data + '.jsonl'))
        observed = subset(dataset, {p.case_id for p in rows})
        score = compare(observed, rows, rows)['candidate']
        score.update({
            'planned_n': len(dataset), 'observed_n': len(rows),
            'predictions_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'budget_errors': sum(p.status == 'error' and '402' in (p.error_message or '') for p in rows),
        })
        trace = ROOT / run / (run + '_trace.jsonl')
        if trace.exists():
            score['recorded_api_cost_usd'] = sum(
                (json.loads(line).get('usage') or {}).get('cost', 0) or 0
                for line in trace.read_text(encoding='utf-8').splitlines())
        report['runs'][run] = score
    dataset = TestDataset.load_jsonl(CASES / 'benchmark_100.jsonl')
    baseline, candidate = read('v32_baseline'), read('v32_refs')
    report['original100_including_budget_failures'] = compare(dataset, baseline, candidate)
    report['original100_including_budget_failures']['interpretation'] = (
        'Not a valid model-quality comparison: 35 candidate requests failed due to budget. '
        'Do not promote based on either complete-case accuracy or this aggregate.')
    evaluated_ids = {p.case_id for p in candidate if '402' not in (p.error_message or '')}
    report['non402_subset_descriptive_only'] = compare(
        subset(dataset, evaluated_ids), [p for p in baseline if p.case_id in evaluated_ids],
        [p for p in candidate if p.case_id in evaluated_ids])
    report['non402_subset_descriptive_only']['interpretation'] = (
        'Post-hoc subset, skewed toward easy/medium goods; includes all three evidence failures. '
        'McNemar output is descriptive and cannot establish generalisation.')
    reviewed = TestDataset.load_jsonl(CASES / 'v32_reviewed89.jsonl')
    ids = {c.id for c in reviewed}
    report['reviewed89_including_budget_failures'] = compare(
        reviewed, [p for p in baseline if p.case_id in ids], [p for p in candidate if p.case_id in ids])
    report['reviewed89_including_budget_failures']['interpretation'] = (
        'Four corrected labels and eleven quarantines; this is a different evaluation set, '
        'not an accuracy gain from code changes. Only the fifteen flagged labels were audited.')
    report['control_limitation'] = (
        'Synthetic descriptions locked before runs, labels checked against local DB. '
        'CTRL32-01 was inspected to debug citation copying; control16 is consumed, '
        'not a blind held-out evaluation for subsequent tuning and not expert real-world ground truth.')
    (ROOT / 'v32_summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for run, row in report['runs'].items():
        print(run, row['status'], row['correct']['code_10'], '/', row['observed_n'],
              'cost', round(row.get('recorded_api_cost_usd', 0), 3))
    print('non402 paired', report['non402_subset_descriptive_only']['paired_code_10'])


if __name__ == '__main__':
    main()
