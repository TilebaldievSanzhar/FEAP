"""Baseline of the cascade on a data set: what the verifier will have to work with.

Pre-registration §3 (R0): outcome light on all cases and on the calibration pool, the
level where wrong codes go wrong, whether the gold code was among the candidates the
cascade itself considered (trace L3–L5), cost and time per case.

The level decides the order of the next stages: if more than a third of the wrong
codes have the wrong group, navigation (stage 2) goes before the compiler (stage 3).

Run:
    python -m benchmark.scripts.baseline_report --run-id r0_v32_dev150 \\
        --cases benchmark/data/cases/v2/eaeu_pd_dev150.jsonl \\
        --predictions benchmark/data/results/r0_v32_dev150/r0_v32_dev150_predictions.jsonl \\
        --trace benchmark/data/results/r0_v32_dev150/r0_v32_dev150_trace.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from benchmark.evaluator.budget import case_costs
from benchmark.holdout import Holdout
from benchmark.metrics.outcome import Outcome, case_outcome, summarize
from benchmark.metrics.stats import share
from benchmark.schema.case_v2 import load_cases
from benchmark.schema.prediction import load_predictions

#: Trace schema → (level, field): candidates of each level of the cascade.
LEVELS = {
    'GroupResult': ('L2', 'group'),
    'ShortlistResult': ('L3', None),
    'PositionResult': ('L3', 'position'),
    'SubpositionResult': ('L4', 'subposition'),
    'SubsubpositionShortlistResult': ('L5', 'code_8'),
    'SubsubpositionResult': ('L5', 'code_10'),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--predictions', type=Path, required=True)
    p.add_argument('--trace', type=Path, required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--out-dir', type=Path)
    return p.parse_args()


def candidates(trace: Path) -> dict[str, dict[str, set[str]]]:
    """Codes the cascade considered, per case and level."""
    found: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    with trace.open(encoding='utf-8') as f:
        for line in f:
            record = json.loads(line)
            name = ((record.get('response_format') or {}).get('json_schema') or {}).get('name')
            if name not in LEVELS or not record.get('case_id'):
                continue
            try:
                data = json.loads(record.get('raw_content') or '')
            except json.JSONDecodeError:
                continue
            level, field = LEVELS[name]
            items = data.get('shortlisted') if field is None else [
                c.get(field) for c in data.get('candidates') or [] if isinstance(c, dict)]
            for item in items or []:
                code = re.sub(r'\D', '', str(item or ''))
                if code:
                    found[record['case_id']][level].add(code)
    return found


def error_level(gold: str, predicted: str) -> str:
    """First level where the predicted code leaves the gold one."""
    for digits, level in ((2, 'group'), (4, 'heading'), (6, 'subheading'), (8, 'eight_digit')):
        if len(gold) >= digits and predicted[:digits] != gold[:digits]:
            return level
    return 'national' if len(gold) == 10 else 'beyond_gold'


def coverage(gold: str, seen: dict[str, set[str]]) -> dict[str, bool | None]:
    """Was the gold code (at the precision of each level) among the cascade's own candidates?"""
    def among(level: str, digits: int) -> bool | None:
        if len(gold) < digits:
            return None
        return any(code[:digits] == gold[:digits] for code in seen.get(level, ()) if len(code) >= digits)
    return {'L2_group': among('L2', 2), 'L3_heading': among('L3', 4), 'L4_subheading': among('L4', 6),
            'L5_leaf': among('L5', len(gold)) if len(gold) >= 8 else None}


def section(cases, predictions, seen, costs) -> dict:
    outcomes = [o for o in (case_outcome(c, predictions.get(c.id)) for c in cases) if o.scored]
    wrong = [o for o in outcomes if o.wrong_code]
    levels = Counter(error_level(o.gold_code, o.predicted_code) for o in wrong)
    cover = {key: Counter() for key in ('L2_group', 'L3_heading', 'L4_subheading', 'L5_leaf')}
    for o in wrong:
        for key, value in coverage(o.gold_code, seen.get(o.case_id, {})).items():
            if value is not None:
                cover[key][value] += 1
    latency = sorted(p.latency_ms for c in cases if (p := predictions.get(c.id)) and p.latency_ms)
    spent = [costs.get(c.id, 0.0) for c in cases if c.id in predictions]
    return {
        'light': summarize(outcomes),
        'wrong_codes': len(wrong),
        'error_level': {level: share(levels[level], len(wrong)) for level in
                        ('group', 'heading', 'subheading', 'eight_digit', 'national')},
        'gold_among_candidates_of_wrong': {key: share(c[True], c[True] + c[False]) for key, c in cover.items()},
        'stage2_first': levels['group'] > len(wrong) / 3 if wrong else None,
        'cost_usd': {'total': round(sum(spent), 4), 'per_case': round(sum(spent) / len(spent), 5) if spent else None},
        'latency_s': {'p50': round(latency[len(latency) // 2] / 1000, 1) if latency else None,
                      'p95': round(latency[min(len(latency) - 1, int(0.95 * len(latency)))] / 1000, 1)
                      if latency else None},
        'wrong': [{'case_id': o.case_id, 'gold': o.gold_code, 'predicted': o.predicted_code,
                   'flagged': o.flagged, 'level': error_level(o.gold_code, o.predicted_code),
                   'coverage': coverage(o.gold_code, seen.get(o.case_id, {}))} for o in wrong],
    }


def report(run_id: str, parts: dict[str, dict]) -> str:
    def pct(s):
        return '—' if not s or s.get('share') is None else f"{s['share']:.0%} ({s['k']}/{s['n']})"

    lines = [f'# Baseline: {run_id}', '']
    for name, part in parts.items():
        light = part['light']
        lines += [f'## {name}', '', '| ✅ | 🟡 | 🔴 | ⚫ |', '|---|---|---|---|',
                  '| ' + ' | '.join(pct(light['outcomes'][o.value]) for o in Outcome) + ' |', '',
                  f"Неверных кодов {part['wrong_codes']}; флаг каскада на них "
                  f"{pct(light['flags']['flag_recall_on_wrong'])}, на верных "
                  f"{pct(light['flags']['false_alarm_on_correct'])}.", '',
                  '| Где ошибка | доля неверных |', '|---|---|']
        lines += [f'| {level} | {pct(s)} |' for level, s in part['error_level'].items()]
        lines += ['', '| Эталон среди кандидатов трассы (у неверных) | доля |', '|---|---|']
        lines += [f'| {level} | {pct(s)} |' for level, s in part['gold_among_candidates_of_wrong'].items()]
        lines += ['', f"Стоимость ${part['cost_usd']['total']} (${part['cost_usd']['per_case']} на кейс); "
                      f"время p50 {part['latency_s']['p50']} с, p95 {part['latency_s']['p95']} с. "
                      f"Этап 2 раньше Этапа 3: {'да' if part['stage2_first'] else 'нет'}.", '']
    return '\n'.join(lines)


def main() -> None:
    args = parse_args()
    cases = load_cases(args.cases)
    predictions = {p.case_id: p for p in load_predictions(args.predictions)}
    seen = candidates(args.trace)
    costs = case_costs(args.trace)
    calibration = Holdout.load().pool(cases, 'calibration').cases
    parts = {'all': section(cases, predictions, seen, costs)}
    if len(calibration) != len(cases):
        parts['calibration'] = section(calibration, predictions, seen, costs)
    out_dir = args.out_dir or args.predictions.parent
    (out_dir / f'{args.run_id}_baseline.json').write_text(
        json.dumps(parts, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    text = report(args.run_id, parts)
    (out_dir / f'{args.run_id}_baseline.md').write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
