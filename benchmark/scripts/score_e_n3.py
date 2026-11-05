"""E-N3: the cascade with precedents at L3 (C1) against the same cascade without (C0).

Reads both runs on the same cases and applies docs/navigation_e_n3.md §3:

- primary: 10-digit accuracy C1 − C0, paired bootstrap over cases (10 000, seed 20261105);
- secondary: 4- and 2-digit accuracy, outcome light, cost and time per case, the cases
  where L3 moved to another group and how they ended;
- guards: silent errors of C1 at most C0 + 3 points, cost per case at most C0 + 40%.

Run:
    python -m benchmark.scripts.score_e_n3 --cases benchmark/data/cases/v2/eaeu_pd_fresh150.jsonl \\
        --c0 benchmark/data/results/e_n3_c0_fresh150 --c1 benchmark/data/results/e_n3_c1_fresh150
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from benchmark.evaluator.budget import case_costs
from benchmark.metrics.outcome import Outcome, case_outcome
from benchmark.metrics.stats import share
from benchmark.schema.case_v2 import load_cases
from benchmark.schema.prediction import load_predictions

SEED = 20261105
MOVED = 'выбрана на уровне 3 среди кандидатов из похожих решений'


def bootstrap(pairs: list[tuple[bool, bool]]) -> dict:
    rng = random.Random(SEED)
    n = len(pairs)
    point = sum(a - b for a, b in pairs) / n
    diffs = sorted(sum(a - b for a, b in (pairs[rng.randrange(n)] for _ in range(n))) / n for _ in range(10_000))
    return {'diff': round(point, 4), 'ci95': [round(diffs[250], 4), round(diffs[9749], 4)]}


def load_run(path: Path) -> tuple[dict, dict, dict]:
    run = path.name
    predictions = {p.case_id: p for p in load_predictions(path / f'{run}_predictions.jsonl')}
    costs = case_costs(path / f'{run}_trace.jsonl')
    manifest = json.loads((path / f'{run}_manifest.json').read_text(encoding='utf-8'))
    return predictions, costs, manifest


def moved(pred) -> bool:
    chain = (((pred.raw_response or {}).get('result') or {}).get('classification_chain') or {})
    return MOVED in ((chain.get('group') or {}).get('reasoning') or '')


def arm_summary(cases, predictions, costs) -> dict:
    outcomes = {c.id: case_outcome(c, predictions.get(c.id)) for c in cases}
    scored = [c for c in cases if outcomes[c.id].scored]
    n = len(scored)

    def acc(digits):
        return share(sum(bool(outcomes[c.id].predicted_code) and outcomes[c.id].predicted_code[:digits]
                         == c.gold.classification.code[:digits] for c in scored), n)
    latency = sorted(p.latency_ms for c in scored if (p := predictions.get(c.id)) and p.latency_ms)
    spent = [costs.get(c.id, 0.0) for c in scored]
    return {
        'n': n,
        'accuracy_10': acc(10), 'accuracy_4': acc(4), 'accuracy_2': acc(2),
        'light': {o.value: share(sum(outcomes[c.id].outcome is o for c in scored), n) for o in Outcome},
        'usd_per_case': round(sum(spent) / n, 5), 'usd_total': round(sum(spent), 4),
        'latency_s_p50': round(latency[len(latency) // 2] / 1000, 1) if latency else None,
        'latency_s_p95': round(latency[min(len(latency) - 1, int(0.95 * len(latency)))] / 1000, 1)
        if latency else None,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--c0', type=Path, required=True)
    p.add_argument('--c1', type=Path, required=True)
    p.add_argument('--out', type=Path)
    args = p.parse_args()

    cases = load_cases(args.cases)
    p0, cost0, m0 = load_run(args.c0)
    p1, cost1, m1 = load_run(args.c1)
    same = [k for k in ('dataset_sha256', 'models', 'source_sha256', 'reference_data', 'generation',
                        'evidence_policy') if m0.get(k) != m1.get(k)]
    if same:
        raise SystemExit(f'C0 and C1 differ in {same}: not one commit and configuration')
    if m0['precedents']['enabled'] or not m1['precedents']['enabled']:
        raise SystemExit('C0 must run without precedents and C1 with them')

    s0, s1 = arm_summary(cases, p0, cost0), arm_summary(cases, p1, cost1)
    scored = [c for c in cases if case_outcome(c, p0.get(c.id)).scored]

    def right(preds, case, digits):
        pred = preds.get(case.id)
        code = pred.predicted_code_10 if pred and pred.status == 'completed' else None
        return bool(code) and code[:digits] == case.gold.classification.code[:digits]
    diffs = {f'accuracy_{d}': bootstrap([(right(p1, c, d), right(p0, c, d)) for c in scored]) for d in (10, 4, 2)}
    changes = {f'fixed_{d}': sum(right(p1, c, d) and not right(p0, c, d) for c in scored) for d in (10, 4)}
    changes |= {f'broken_{d}': sum(right(p0, c, d) and not right(p1, c, d) for c in scored) for d in (10, 4)}
    moved_cases = [c for c in scored if p1.get(c.id) and moved(p1[c.id])]
    moved_summary = {
        'n': len(moved_cases),
        'c1_right_10': sum(right(p1, c, 10) for c in moved_cases),
        'c1_right_4': sum(right(p1, c, 4) for c in moved_cases),
        'c0_right_4': sum(right(p0, c, 4) for c in moved_cases),
        'cases': [{'case_id': c.id, 'gold': c.gold.classification.code,
                   'c0': p0[c.id].predicted_code_10 if c.id in p0 else None,
                   'c1': p1[c.id].predicted_code_10} for c in moved_cases],
    }

    d10 = diffs['accuracy_10']
    silent0 = s0['light']['silent_error']['share'] or 0
    silent1 = s1['light']['silent_error']['share'] or 0
    guards = {'silent_errors': silent1 <= silent0 + 0.03,
              'cost': s1['usd_per_case'] <= s0['usd_per_case'] * 1.4}
    if d10['diff'] >= 0.05 and d10['ci95'][0] > 0:
        verdict = 'H-N3 подтверждена' if all(guards.values()) else 'частично (нарушено ограничение)'
    elif d10['diff'] > 0:
        verdict = 'частично'
    else:
        verdict = 'не подтверждена'
    summary = {'C0': s0, 'C1': s1, 'C1_minus_C0': diffs, 'changes': changes, 'moved': moved_summary,
               'guards': guards, 'verdict': verdict,
               'runs': {'c0': args.c0.as_posix(), 'c1': args.c1.as_posix(),
                        'git_revision': m1.get('git_revision'), 'precedents': m1.get('precedents')}}
    out = args.out or args.c1.parent / 'e_n3_fresh150_summary.json'
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: summary[k] for k in ('C1_minus_C0', 'changes', 'guards', 'verdict')}, ensure_ascii=False,
                     indent=2))
    for arm in ('C0', 'C1'):
        s = summary[arm]
        print(f"{arm}: 10 зн. {s['accuracy_10']['share']:.1%}, 4 зн. {s['accuracy_4']['share']:.1%}, "
              f"2 зн. {s['accuracy_2']['share']:.1%}, тихих {s['light']['silent_error']['share']:.1%}, "
              f"${s['usd_per_case']}/кейс, p50 {s['latency_s_p50']} с")
    print(f"перенос в другую группу: {moved_summary['n']} кейсов; C1 верно 4 зн. {moved_summary['c1_right_4']}, "
          f"10 зн. {moved_summary['c1_right_10']}; C0 было верно 4 зн. {moved_summary['c0_right_4']}")


if __name__ == '__main__':
    main()
