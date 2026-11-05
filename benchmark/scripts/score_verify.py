"""Score verifier runs against the cascade outcome (pre-registration §6 and §8).

Per verify run (one arm) and per policy P1–P3:

- recall(W) — share of wrong codes flagged; flags(C) — share of correct codes flagged;
- the same for V0 (the cascade's own flag) and for «V0 or policy», what the user sees;
- paired bootstrap of recall(W) policy − V0 over the wrong codes: 10 000 resamples,
  seed 20261105, percentile 95% interval;
- atomic answers against automatic labels (H2): a condition on the gold path should be T,
  a specific (not «прочие») sibling of a gold-path row should be F — the latter only on
  cases whose rules ⊆ {ОПИ 1, ОПИ 6}; U is not a correct answer;
- cost and latency per case, executor failures.

The H1 decision of §8 is read for V1 only. Cases are restricted to the calibration pool
(``--pool calibration``: dev150 → 148) when thresholds are to be read from them.

Run:
    python -m benchmark.scripts.score_verify --run-id score_passport100 \\
        --cases benchmark/data/cases/v2/passport_regression100.jsonl \\
        --predictions benchmark/data/results/v32_baseline/v32_baseline_predictions.jsonl \\
        --verify benchmark/data/results/verify/verify_v1_passport100
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from contextlib import closing
from pathlib import Path

import psycopg2

from benchmark.evaluator.fingerprint import database_fingerprint, libpq_dsn
from benchmark.holdout import Holdout
from benchmark.scripts.baseline_report import error_level
from benchmark.metrics.outcome import CaseOutcome, Outcome, case_outcome
from benchmark.metrics.stats import share
from benchmark.schema.case_v2 import CaseV2, load_cases
from benchmark.schema.prediction import load_predictions
from feap.decision.contracts import ConditionKind, Status, VerifyTrace
from feap.decision.rows import PositionRows, load_positions
from feap.decision.signals import POLICIES

RESULTS_ROOT = Path('benchmark/data/results/verify')
SEED = 20261105
RESAMPLES = 10_000
SIMPLE_RULES = {'ОПИ 1', 'ОПИ 6'}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--predictions', type=Path, required=True)
    p.add_argument('--verify', type=Path, action='append', required=True, help='Run directory or *_verify.jsonl')
    p.add_argument('--run-id', required=True)
    p.add_argument('--pool', choices=['all', 'calibration'], default='all')
    p.add_argument('--out-dir', type=Path)
    return p.parse_args()


def load_run(path: Path) -> tuple[str, dict[str, VerifyTrace], dict | None]:
    files = [path] if path.is_file() else sorted(path.glob('*_verify.jsonl'))
    if len(files) != 1:
        raise SystemExit(f'{path}: expected one *_verify.jsonl, found {len(files)}')
    traces = {}
    for line in files[0].read_text(encoding='utf-8').splitlines():
        if line.strip():
            trace = VerifyTrace.model_validate_json(line)
            traces[trace.case_id] = trace
    manifest_path = files[0].with_name(files[0].name.replace('_verify.jsonl', '_manifest.json'))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else None
    arms = {t.arm for t in traces.values()}
    return (arms.pop() if len(arms) == 1 else files[0].stem), traces, manifest


def flagged(trace: VerifyTrace | None, policy: str) -> bool:
    """A missing trace or a verifier error is no signal (plan §5.2)."""
    return bool(trace and not trace.error and trace.flags.get(policy))


def bootstrap_diff(pairs: list[tuple[bool, bool]]) -> dict | None:
    """Paired percentile bootstrap of mean(a) − mean(b) over cases."""
    if not pairs:
        return None
    rng = random.Random(SEED)
    n = len(pairs)
    point = sum(a - b for a, b in pairs) / n
    diffs = sorted(sum(a - b for a, b in (pairs[rng.randrange(n)] for _ in range(n))) / n
                   for _ in range(RESAMPLES))
    return {'diff': round(point, 4), 'ci95': [round(diffs[int(0.025 * RESAMPLES)], 4),
                                              round(diffs[int(0.975 * RESAMPLES) - 1], 4)]}


def h1(outcomes: list[CaseOutcome], traces: dict[str, VerifyTrace]) -> dict:
    wrong = [o for o in outcomes if o.wrong_code]
    right = [o for o in outcomes if o.outcome is Outcome.CORRECT]
    out = {'V0': {'recall_wrong': share(sum(o.flagged for o in wrong), len(wrong)),
                  'flags_correct': share(sum(o.flagged for o in right), len(right))}}
    for policy in POLICIES:
        w = [flagged(traces.get(o.case_id), policy) for o in wrong]
        c = [flagged(traces.get(o.case_id), policy) for o in right]
        either_w = [f or o.flagged for f, o in zip(w, wrong)]
        either_c = [f or o.flagged for f, o in zip(c, right)]
        out[policy] = {
            'recall_wrong': share(sum(w), len(wrong)),
            'flags_correct': share(sum(c), len(right)),
            'minus_v0': bootstrap_diff([(f, o.flagged) for f, o in zip(w, wrong)]),
            'with_v0': {'recall_wrong': share(sum(either_w), len(wrong)),
                        'flags_correct': share(sum(either_c), len(right)),
                        'silent_errors_left': len(wrong) - sum(either_w)},
        }
    return out


def h1_decision(rows: dict) -> str:
    """§8 of the pre-registration, read on V1."""
    def ok(row, recall, flags):
        return (row['recall_wrong']['share'] or 0) >= recall and (row['flags_correct']['share'] or 0) <= flags
    policies = [rows[p] for p in POLICIES]
    if any(ok(r, 0.5, 0.15) and r['minus_v0'] and r['minus_v0']['ci95'][0] > 0 for r in policies):
        return 'H1 подтверждена'
    if any(ok(r, 0.3, 0.25) for r in policies):
        return 'H1 частично'
    return 'H1 не подтверждена'


def gold_labels(trace: VerifyTrace, case: CaseV2, trees: dict[str, PositionRows]) -> list[tuple[str, str, Status]]:
    """(condition id, type, expected status) for the conditions this run actually checked."""
    gold = case.gold.classification
    tree = trees.get(gold.code[:4])
    node = tree.node(gold.code) if tree else None
    if node is None:
        return []
    conditions = {c.id: c for c in trace.conditions}
    path = [row.id for row in tree.path(node.id)]
    simple = bool(gold.opi_expected) and set(gold.opi_expected) <= SIMPLE_RULES
    labels = []
    for cid in path:
        c = conditions.get(cid)
        if c is None or c.kind is ConditionKind.REGIME:
            continue
        if c.kind is not ConditionKind.RESIDUAL:
            labels.append((cid, c.kind.value, Status.T))
        if c.residual:
            labels.append((cid, 'residual', Status.T))
    if simple:
        for cid in path[1:]:
            for sibling in tree.siblings(cid):
                c = conditions.get(sibling.id)
                if c is None or sibling.id in path or c.kind is ConditionKind.REGIME:
                    continue
                if c.kind is ConditionKind.RESIDUAL:
                    labels.append((sibling.id, 'residual', Status.F))
                elif not c.residual:  # «креветки прочие» рядом с «креветки Pandalidae» может быть верно T
                    labels.append((sibling.id, c.kind.value, Status.F))
    return labels


def h2(traces: dict[str, VerifyTrace], cases: dict[str, CaseV2], trees: dict[str, PositionRows]) -> dict:
    rows: dict[str, dict[str, int]] = {}
    for case_id, trace in traces.items():
        if trace.error or case_id not in cases:
            continue
        statuses = {s.condition_id: s for s in trace.statuses}
        for cid, kind, expected in gold_labels(trace, cases[case_id], trees):
            status = statuses.get(cid)
            if status is None:
                continue
            actual = status.residual if kind == 'residual' else status.own
            r = rows.setdefault(kind, {'n': 0, 'correct': 0, 'false_F': 0, 'n_T': 0, 'false_T': 0, 'n_F': 0, 'U': 0})
            r['n'] += 1
            r['correct'] += actual is expected
            r['U'] += actual is Status.U
            if expected is Status.T:
                r['n_T'] += 1
                r['false_F'] += actual is Status.F
            else:
                r['n_F'] += 1
                r['false_T'] += actual is Status.T
    total = {k: sum(r[k] for r in rows.values()) for k in ('n', 'correct', 'false_F', 'n_T', 'false_T', 'n_F', 'U')}
    rows['all'] = total
    return {kind: {'n': r['n'], 'accuracy': share(r['correct'], r['n']),
                   'false_F_on_gold_path': share(r['false_F'], r['n_T']),
                   'false_T_on_siblings': share(r['false_T'], r['n_F']), 'U': share(r['U'], r['n'])}
            for kind, r in rows.items()}


def costs(traces: dict[str, VerifyTrace]) -> dict:
    done = [t for t in traces.values() if not t.error]
    if not done:
        return {}
    latency = sorted(t.latency_ms for t in done)
    answers = [a for t in done for a in t.answers]
    failed = [a for a in answers if a.status != 'ok']
    return {
        'cases': len(done), 'errors': sorted(t.case_id for t in traces.values() if t.error),
        'usd_per_case': round(statistics.fmean(t.cost_usd for t in done), 6),
        'usd_total': round(sum(t.cost_usd for t in done), 4),
        'latency_ms_p50': latency[len(latency) // 2],
        'latency_ms_p95': latency[min(len(latency) - 1, int(0.95 * len(latency)))],
        'questions_per_case': round(len(answers) / len(done), 2),
        'executor_failures': share(len(failed), len(answers)),
    }


def wrong_cases(outcomes: list[CaseOutcome], runs: dict[str, dict[str, VerifyTrace]]) -> list[dict]:
    rows = []
    for o in outcomes:
        if not o.wrong_code:
            continue
        row = {'case_id': o.case_id, 'gold': o.gold_code, 'predicted': o.predicted_code, 'V0': o.flagged,
               'level': error_level(o.gold_code, o.predicted_code)}
        for arm, traces in runs.items():
            trace = traces.get(o.case_id)
            row[arm] = {'flags': {p: flagged(trace, p) for p in POLICIES},
                        'signals': sorted({f'{s.name}:{s.strength}' for s in trace.signals}) if trace else None,
                        'error': trace.error if trace else 'not run'}
        rows.append(row)
    return rows


def by_level(rows: list[dict], arm: str) -> dict:
    """Recall of wrong codes per level where the cascade went wrong (exploratory, not in §8)."""
    levels = sorted({r['level'] for r in rows}, key=['group', 'heading', 'subheading', 'eight_digit',
                                                       'national', 'beyond_gold'].index)
    out = {}
    for level in levels:
        group = [r for r in rows if r['level'] == level]
        out[level] = {'V0': share(sum(r['V0'] for r in group), len(group)),
                      **{p: share(sum(r[arm]['flags'][p] for r in group), len(group)) for p in POLICIES}}
    return out


def _pct(s: dict | None) -> str:
    if not s or s.get('share') is None:
        return '—'
    return f"{s['share']:.0%} ({s['k']}/{s['n']})"


def report(summary: dict) -> str:
    lines = [f"# Verifier: {summary['run_id']}", '',
             f"Cases: {summary['cases']['scored']} scored, {summary['cases']['wrong']} wrong codes (W), "
             f"{summary['cases']['correct']} correct (C); pool `{summary['pool']}`.", '']
    for arm, data in summary['arms'].items():
        lines += [f'## {arm}', '', '| | recall(W) | flags(C) | − V0, 95% ДИ | с V0: recall(W) | с V0: flags(C) | '
                  'тихих ошибок осталось |', '|---|---|---|---|---|---|---|']
        v0 = data['h1']['V0']
        lines.append(f"| V0 | {_pct(v0['recall_wrong'])} | {_pct(v0['flags_correct'])} | | | | |")
        for policy in POLICIES:
            r = data['h1'][policy]
            diff = r['minus_v0']
            diff_text = f"{diff['diff']:+.0%} [{diff['ci95'][0]:+.0%}; {diff['ci95'][1]:+.0%}]" if diff else '—'
            lines.append(f"| {policy} | {_pct(r['recall_wrong'])} | {_pct(r['flags_correct'])} | {diff_text} | "
                         f"{_pct(r['with_v0']['recall_wrong'])} | {_pct(r['with_v0']['flags_correct'])} | "
                         f"{r['with_v0']['silent_errors_left']} |")
        if data.get('decision'):
            lines += ['', f"**{data['decision']}** (§8 предрегистрации, плечо V1)."]
        lines += ['', '| Тип условия | n | точность | ложные F на пути эталона | ложные T у соседей | U |',
                  '|---|---|---|---|---|---|']
        for kind, r in data['h2'].items():
            lines.append(f"| {kind} | {r['n']} | {_pct(r['accuracy'])} | {_pct(r['false_F_on_gold_path'])} | "
                         f"{_pct(r['false_T_on_siblings'])} | {_pct(r['U'])} |")
        levels = data.get('by_level') or {}
        if levels:
            lines += ['', '| Где ошибся каскад | n | V0 | ' + ' | '.join(POLICIES) + ' |',
                      '|---|---|---|' + '---|' * len(POLICIES)]
            for level, r in levels.items():
                lines.append(f"| {level} | {r['V0']['n']} | {_pct(r['V0'])} | "
                             + ' | '.join(_pct(r[p]) for p in POLICIES) + ' |')
        c = data['costs']
        if c:
            lines += ['', f"Стоимость ${c['usd_per_case']:.4f} на кейс (${c['usd_total']:.2f} всего), "
                          f"p50 {c['latency_ms_p50'] / 1000:.1f} с, p95 {c['latency_ms_p95'] / 1000:.1f} с, "
                          f"{c['questions_per_case']} вопросов на кейс, сбоев исполнителя "
                          f"{_pct(c['executor_failures'])}, ошибок верификатора {len(c['errors'])}.", '']
    lines += ['## Неверные коды', '', '| Кейс | эталон | каскад | уровень | V0 | ' + ' | '.join(summary['arms']) + ' |',
              '|---|---|---|---|---|' + '---|' * len(summary['arms'])]
    for row in summary['wrong_cases']:
        cells = []
        for arm in summary['arms']:
            a = row[arm]
            flags = ''.join(p[-1] if on else '·' for p, on in a['flags'].items())
            cells.append(f"{flags} {' '.join(a['signals'] or [])}".strip() if not a['error'] else a['error'])
        lines.append(f"| {row['case_id']} | {row['gold']} | {row['predicted']} | {row['level']} | "
                     f"{'⚑' if row['V0'] else ''} | "
                     + ' | '.join(cells) + ' |')
    return '\n'.join(lines) + '\n'


def main() -> None:
    args = parse_args()
    cases = load_cases(args.cases)
    if args.pool == 'calibration':
        cases = Holdout.load().pool(cases, 'calibration').cases
    by_id = {c.id: c for c in cases}
    predictions = {p.case_id: p for p in load_predictions(args.predictions)}
    outcomes = [case_outcome(c, predictions.get(c.id)) for c in cases]
    outcomes = [o for o in outcomes if o.scored]

    runs, manifests = {}, {}
    for path in args.verify:
        arm, traces, manifest = load_run(path)
        runs[arm], manifests[arm] = traces, manifest
    current = database_fingerprint()
    stale = [arm for arm, m in manifests.items() if m and m.get('reference_data') != current]
    if stale:
        raise SystemExit(f'Reference data changed since the run of {stale}: row ids may differ, score on the same DB')
    with closing(psycopg2.connect(libpq_dsn())) as connection, connection.cursor() as cursor:
        trees = load_positions(cursor, {c.gold.classification.code[:4] for c in cases if c.gold.classification})

    summary = {
        'run_id': args.run_id, 'pool': args.pool,
        'cases': {'scored': len(outcomes), 'wrong': sum(o.wrong_code for o in outcomes),
                  'correct': sum(o.outcome is Outcome.CORRECT for o in outcomes)},
        'arms': {}, 'wrong_cases': wrong_cases(outcomes, runs),
    }
    for arm, traces in runs.items():
        rows = h1(outcomes, traces)
        summary['arms'][arm] = {
            'h1': rows, 'decision': h1_decision(rows) if arm == 'V1' else None,
            'h2': h2(traces, by_id, trees), 'costs': costs(traces),
            'by_level': by_level(summary['wrong_cases'], arm),
            'manifest': {k: (manifests[arm] or {}).get(k) for k in ('git_revision', 'model', 'questions_version')},
        }
    out_dir = args.out_dir or RESULTS_ROOT / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f'{args.run_id}_summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    text = report(summary)
    (out_dir / f'{args.run_id}_report.md').write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
