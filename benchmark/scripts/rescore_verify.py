"""Exploratory: replay cached verifier answers under other answer thresholds, without API calls.

Stage 1 turned a decision-model answer into T only if p(supported) ≥ 0.5 and into F only
if p(contradicted) ≥ 0.8 (``feap.decision.backend``); flash-lite has no distribution, so its
label was taken as is. This script feeds every run's stored answers back through the same
``verify_case`` (same conditions, facts and logic) with the thresholds patched, checks that
the frozen thresholds reproduce the stored statuses exactly, and scores H1 and H2 with
``score_verify``'s own functions.

It does not change any pre-registered decision: the result is a post-hoc reading of the
same dev148 data.

Run:
    python -m benchmark.scripts.rescore_verify --run-id rescore_argmax_dev148 --pool calibration \\
        --cases benchmark/data/cases/v2/eaeu_pd_dev150.jsonl \\
        --predictions benchmark/data/results/r0_v32_dev150/r0_v32_dev150_predictions.jsonl \\
        --verify benchmark/data/results/verify/verify_v1_dev150 \\
        --verify benchmark/data/results/verify/verify_v2_dev150 \\
        --verify benchmark/data/results/verify/verify_v3_jev_dev150 \\
        --verify benchmark/data/results/verify/verify_v3_d1_dev150
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
from contextlib import closing
from pathlib import Path

import psycopg2

import feap.decision.backend as backend
from benchmark.evaluator.fingerprint import libpq_dsn
from benchmark.holdout import Holdout
from benchmark.metrics.outcome import case_outcome
from benchmark.schema.case_v2 import load_cases
from benchmark.schema.prediction import load_predictions
from benchmark.scripts.score_verify import RESULTS_ROOT, h1, h2, load_run
from feap.decision.contracts import VerifyTrace
from feap.decision.rows import load_headings, load_positions
from feap.decision.verifier import verify_case

#: name → (T threshold, F threshold); the first one is the frozen stage-1 rule.
SETTINGS = {'stage 1: T ≥ 0.5, F ≥ 0.8': (0.5, 0.8), 'argmax': (0.0, 0.0)}


class CachedAnswers:
    """A backend that returns the answers a run already got."""

    def __init__(self, trace: VerifyTrace):
        self.name = trace.arm
        self.by_id = {a.question_id: a for a in trace.answers}

    async def answer(self, questions, spans):
        return [self.by_id[q.id] for q in questions if q.id in self.by_id], []


def traces_of(path: Path) -> Path:
    """Run directory → its *_verify.jsonl, unpacking the committed .gz if needed."""
    plain = sorted(path.glob('*_verify.jsonl'))
    if plain:
        return path
    packed = sorted(path.glob('*_verify.jsonl.gz'))
    if len(packed) != 1:
        raise SystemExit(f'{path}: no *_verify.jsonl(.gz)')
    target = packed[0].with_suffix('')
    target.write_bytes(gzip.decompress(packed[0].read_bytes()))
    return path


def share(row: dict, key: str) -> float | None:
    return (row.get(key) or {}).get('share')


def pct(x: float | None) -> str:
    return '—' if x is None else f'{x:.0%}'


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--predictions', type=Path, required=True)
    p.add_argument('--verify', type=Path, action='append', required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--pool', choices=['all', 'calibration'], default='all')
    args = p.parse_args()

    cases = load_cases(args.cases)
    if args.pool == 'calibration':
        cases = Holdout.load().pool(cases, 'calibration').cases
    by_id = {c.id: c for c in cases}
    predictions = {p.case_id: p for p in load_predictions(args.predictions)}
    outcomes = [o for o in (case_outcome(c, predictions.get(c.id)) for c in cases) if o.scored]
    runs = {}
    for path in args.verify:
        arm, traces, manifest = load_run(traces_of(path))
        runs[arm] = (traces, manifest)
    positions = {c.gold.classification.code[:4] for c in cases}
    for traces, _ in runs.values():
        for t in traces.values():
            positions |= {t.chosen_code[:4], *(a[:4] for a in t.alternatives)}
    with closing(psycopg2.connect(libpq_dsn())) as connection, connection.cursor() as cursor:
        trees = load_positions(cursor, positions)
        headings = load_headings(cursor, {p[:2] for p in positions})

    rows = []
    for setting, (t_threshold, f_threshold) in SETTINGS.items():
        backend.T_THRESHOLD, backend.F_THRESHOLD = t_threshold, f_threshold
        frozen = setting == next(iter(SETTINGS))
        for arm, (traces, manifest) in runs.items():
            has_distribution = any(a.distribution for t in traces.values() for a in t.answers)
            if not frozen and not has_distribution:
                continue  # thresholds do not apply to labels without a distribution
            replayed, mismatches = {}, 0
            for case_id, trace in traces.items():
                if case_id not in by_id or trace.error:
                    continue
                new = await verify_case(case_id=case_id, description=by_id[case_id].input.description,
                                        chosen_code=trace.chosen_code, alternatives=trace.alternatives, trees=trees,
                                        backend=CachedAnswers(trace), facts=trace.facts,
                                        cap=(manifest or {}).get('cap', 15), headings=headings)
                if frozen:
                    mismatches += ([(s.condition_id, s.status) for s in new.statuses]
                                   != [(s.condition_id, s.status) for s in trace.statuses])
                replayed[case_id] = new
            if frozen and mismatches:
                raise SystemExit(f'{arm}: the replay differs from the stored statuses in {mismatches} cases')
            flags, atomic = h1(outcomes, replayed), h2(replayed, by_id, trees)
            rows.append({'arm': arm, 'setting': setting, 'h1': {k: flags[k] for k in ('P1', 'P2', 'P3')},
                         'h2': atomic})
    backend.T_THRESHOLD, backend.F_THRESHOLD = SETTINGS[next(iter(SETTINGS))]

    lines = [f'# Verifier answers under other thresholds: {args.run_id}', '',
             f'Cases: {len(outcomes)} scored, pool `{args.pool}`. Post-hoc reading of cached answers; the '
             'pre-registered decision is unchanged. The frozen thresholds reproduce the stored statuses exactly.', '',
             '| Arm | Thresholds | atomic accuracy | U | false F on gold path | false T on siblings | precision of T/F '
             '| P1 recall(W) / flags(C) | P2 recall(W) / flags(C) |', '|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        a = r['h2']['all']
        acc, u = share(a, 'accuracy'), share(a, 'U')
        decisive = acc / (1 - u) if acc is not None and u is not None and u < 1 else None
        p1, p2 = r['h1']['P1'], r['h1']['P2']
        lines.append(f"| {r['arm']} | {r['setting']} | {pct(acc)} | {pct(u)} | {pct(share(a, 'false_F_on_gold_path'))} "
                     f"| {pct(share(a, 'false_T_on_siblings'))} | {pct(decisive)} "
                     f"| {pct(share(p1, 'recall_wrong'))} / {pct(share(p1, 'flags_correct'))} "
                     f"| {pct(share(p2, 'recall_wrong'))} / {pct(share(p2, 'flags_correct'))} |")
    out_dir = RESULTS_ROOT / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f'{args.run_id}_summary.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    (out_dir / f'{args.run_id}_report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))


if __name__ == '__main__':
    asyncio.run(main())
