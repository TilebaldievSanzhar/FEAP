"""Summarise complete paired probes without treating repeats as independent goods."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def summarize(rows):
    pairs = defaultdict(dict)
    for row in rows:
        key = (row['case_id'], row['repeat'])
        name = row['variant']
        if name not in {'control', 'variant'} or name in pairs[key]:
            raise ValueError(f'Unexpected or duplicate variant for {key}: {name}')
        pairs[key][name] = row
    if not pairs or any(set(pair) != {'control', 'variant'} for pair in pairs.values()):
        raise ValueError('Every case/repeat must have both control and variant')
    if any(a['control']['parent_correct'] != a['variant']['parent_correct']
           for a in pairs.values()):
        raise ValueError('Parent context differs within a pair')
    variants = {}
    for name in ('control', 'variant'):
        group = [pair[name] for pair in pairs.values()]
        variants[name] = {
            'n': len(group), 'correct': sum(r['correct'] and not r['error'] for r in group),
            'errors': sum(bool(r['error']) for r in group),
            'parent_correct_n': sum(r['parent_correct'] for r in group),
            'parent_correct_hits': sum(r['parent_correct'] and r['correct'] and not r['error']
                                       for r in group),
            'api_calls': sum(len(r['calls']) for r in group),
            'api_usage_cost_usd': sum((call.get('usage') or {}).get('cost', 0) or 0
                                      for r in group for call in r['calls']),
        }
    outcomes = []
    for (cid, repeat), pair in sorted(pairs.items()):
        outcomes.append({
            'case_id': cid, 'repeat': repeat,
            'parent_correct': pair['control']['parent_correct'],
            'expected': pair['control']['expected'],
            **{name: {'selected': row['selected'], 'correct': row['correct'] and not row['error'],
                      'error': bool(row['error'])} for name, row in pair.items()},
        })
    return {
        'unique_cases': len({cid for cid, _ in pairs}), 'paired_trials': len(pairs),
        'note': 'Repeats share goods and contexts; trial counts are not independent sample sizes.',
        'variants': variants, 'outcomes': outcomes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding='utf-8').splitlines()]
    manifest_path = args.input.with_suffix('.manifest.json')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    expected = {(cid, repeat) for cid in manifest['case_ids']
                for repeat in range(manifest['repeat'])}
    if {(r['case_id'], r['repeat']) for r in rows} != expected:
        raise ValueError('Probe is incomplete or contains cases outside its manifest')
    report = summarize(rows)
    report['input_sha256'] = hashlib.sha256(args.input.read_bytes()).hexdigest()
    report['manifest'] = manifest
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['variants'], indent=2))


if __name__ == '__main__':
    main()
