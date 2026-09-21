"""Measure L5a candidate recall conditional on a correct six-digit parent."""
import argparse
import json
from pathlib import Path

from benchmark.schema.test_case import TestDataset


def score(trace, dataset):
    cases = {c.id: c for c in dataset}
    last = {}
    for row in trace:
        schema = (row.get('response_format') or {}).get('json_schema', {})
        if schema.get('name') != 'SubsubpositionShortlistResult' or row['case_id'] not in cases:
            continue
        try:
            result = json.loads(row['raw_content'])
        except (ValueError, TypeError):
            continue
        last[row['case_id']] = result
    rows = []
    for cid, result in sorted(last.items()):
        parent = ''.join(result.get('subposition', '').split()).replace('.', '')
        selected = result.get('selected_code_8', '')
        branches = [selected]
        for c in sorted(result.get('candidates', []), key=lambda c: c.get('confidence', 0), reverse=True):
            if c['code_8'] not in branches:
                branches.append(c['code_8'])
        gt = cases[cid].ground_truth
        rows.append({'case_id': cid, 'parent_correct': parent == gt.subheading,
                     'selected_hit': selected == gt.code_8, 'top2_hit': gt.code_8 in branches[:2]})
    valid = [r for r in rows if r['parent_correct']]
    return {'n_calls': len(rows), 'n_correct_parent': len(valid),
            'selected_recall': sum(r['selected_hit'] for r in valid) / len(valid) if valid else None,
            'top2_recall': sum(r['top2_hit'] for r in valid) / len(valid) if valid else None,
            'cases': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = score([json.loads(x) for x in args.trace.read_text(encoding='utf-8').splitlines()],
                   TestDataset.load_jsonl(args.dataset))
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
