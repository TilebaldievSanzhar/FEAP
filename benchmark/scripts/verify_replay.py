"""Replay of the condition verifier on recorded cascade runs (decision engine, stage 1, E2-lite).

For every case where the cascade returned a code the script builds the conditions
from raw ETT row texts (the code's path, siblings at each fork, alternatives from
the cascade trace), asks one arm, and writes a ``VerifyTrace`` per case.

- ``--arm DRY``: no calls. Conditions, questions and a cost estimate per arm;
- ``--arm V1`` / ``V2``: flash-lite via OpenRouter, one question per call / all in one;
- numbers are extracted once per data set and cached in ``--facts``; every arm reads
  the same values, so arms differ only in the semantic answers (pre-registration §4);
- holdout cases are refused (``benchmark.holdout``, replay rule) unless ``--allow-holdout``;
- a paid run estimates its cost first and compares it with the OpenRouter balance;
  HTTP 402 stops the run and the remaining cases are left for resume.

Run:
    python -m benchmark.scripts.verify_replay --arm DRY --run-id verify_dry_passport100 \\
        --cases benchmark/data/cases/v2/passport_regression100.jsonl \\
        --predictions benchmark/data/results/v32_baseline/v32_baseline_predictions.jsonl \\
        --trace benchmark/data/results/v32_baseline/v32_baseline_trace.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import statistics
import subprocess
from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import psycopg2

from benchmark.evaluator.budget import openrouter_remaining_usd
from benchmark.evaluator.fingerprint import database_fingerprint, libpq_dsn
from benchmark.holdout import MANIFEST as HOLDOUT_MANIFEST
from benchmark.holdout import Holdout, file_sha256
from benchmark.schema.case_v2 import CaseV2, load_cases
from benchmark.schema.prediction import CasePrediction, load_predictions
from feap.decision import backend as decision_backend
from feap.decision.backend import ASSORTMENT_QUESTION, estimate_tokens, question_text, spans_block
from feap.decision.contracts import DecisionAnswer, EvidenceFact, VerifyTrace
from feap.decision.providers import openrouter as llm
from feap.decision.rows import PositionRows, load_headings, load_positions
from feap.decision.signals import POLICIES
from feap.decision.verifier import QUESTIONS_VERSION, verify_case

RESULTS_ROOT = Path('benchmark/data/results/verify')
#: Trace schema → candidate field: alternatives the cascade itself considered.
_CANDIDATES = {
    'ShortlistResult': None,  # L3a: 'shortlisted' — list of headings
    'PositionResult': 'position',
    'SubpositionResult': 'subposition',
    'SubsubpositionShortlistResult': 'code_8',
    'SubsubpositionResult': 'code_10',
}
#: Output tokens per answer, used only for the estimate.
_OUT_SINGLE, _OUT_BATCH_PER_Q, _OUT_EXTRACT = 40, 30, 60


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--predictions', type=Path, required=True)
    p.add_argument('--trace', type=Path, help='evaluate_audited trace: alternatives of the cascade')
    p.add_argument('--arm', choices=['DRY', 'V1', 'V2'], required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--results-dir', type=Path)
    p.add_argument('--facts', type=Path, help='Shared cache of extracted numbers (default: per data set)')
    p.add_argument('--model', default=llm.DEFAULT_MODEL)
    p.add_argument('--cap', type=int, default=15, help='Siblings kept at a wide fork')
    p.add_argument('--concurrency', type=int, default=2, help='Cases in parallel')
    p.add_argument('--question-concurrency', type=int, default=8, help='V1 calls in parallel per case')
    p.add_argument('--case-id', action='append', default=[])
    p.add_argument('--limit', type=int)
    p.add_argument('--max-usd', type=float, help='Refuse to start if the estimate is higher')
    p.add_argument('--allow-holdout', action='store_true', help='Stage 4 only: run on frozen test sets')
    p.add_argument('--no-resume', action='store_true')
    return p.parse_args()


def trace_alternatives(path: Path | None) -> dict[str, list[str]]:
    """Codes the cascade considered at L3–L5, per case, in the order of the trace."""
    found: dict[str, dict[str, None]] = defaultdict(dict)
    if path is None:
        return {}
    with path.open(encoding='utf-8') as f:
        for line in f:
            record = json.loads(line)
            name = ((record.get('response_format') or {}).get('json_schema') or {}).get('name')
            if name not in _CANDIDATES or not record.get('case_id'):
                continue
            try:
                data = json.loads(record.get('raw_content') or '')
            except json.JSONDecodeError:
                continue
            field = _CANDIDATES[name]
            items = data.get('shortlisted') if field is None else [
                c.get(field) for c in data.get('candidates') or [] if isinstance(c, dict)]
            for item in items or []:
                code = re.sub(r'\D', '', str(item or ''))
                if len(code) >= 4:
                    found[record['case_id']][code] = None
    return {case_id: list(codes) for case_id, codes in found.items()}


def runnable(cases: list[CaseV2], predictions: dict[str, CasePrediction]) -> list[tuple[CaseV2, CasePrediction]]:
    out = []
    for case in cases:
        pred = predictions.get(case.id)
        gold = case.gold.classification
        if gold and gold.scored and pred and pred.status == 'completed' and pred.predicted_code_10:
            out.append((case, pred))
    return out


class DryBackend:
    """No calls: every answer is a dry-run failure, so the trace keeps conditions and questions."""

    name = 'DRY'
    model = 'none'

    async def answer(self, questions, spans):
        return [DecisionAnswer(question_id=q.id, status='error', error='dry run') for q in questions], []


def estimate(case_traces: list[VerifyTrace], prices: dict[str, float],
             cached: dict[str, dict] | None = None) -> dict:
    """Tokens and dollars per case for V1, V2 and the extraction of uncached numbers, without prompt cache."""
    rows = []
    for t in case_traces:
        keys = [k for k in t.facts if k not in ((cached or {}).get(t.case_id) or {})]
        source = spans_block(t.spans)
        questions = t.questions
        q_tokens = [estimate_tokens(question_text(q)) + 60 for q in questions]
        s, single, batch, extract = (estimate_tokens(source), estimate_tokens(llm.SYSTEM_SINGLE),
                                     estimate_tokens(llm.SYSTEM_BATCH), estimate_tokens(llm.SYSTEM_EXTRACT))
        v1_in = sum(single + s + q for q in q_tokens)
        v2_in = batch + s + sum(q_tokens)
        ex_in = len(keys) * (extract + s + 80)
        rows.append({
            'questions': len(questions), 'numeric_keys': len(t.facts),
            'v1_usd': v1_in * prices['prompt'] + len(questions) * _OUT_SINGLE * prices['completion'],
            'v2_usd': v2_in * prices['prompt'] + len(questions) * _OUT_BATCH_PER_Q * prices['completion'],
            'extract_usd': ex_in * prices['prompt'] + len(keys) * _OUT_EXTRACT * prices['completion'],
        })
    if not rows:
        return {}

    def stats(key):
        values = sorted(r[key] for r in rows)
        return {'mean': round(statistics.fmean(values), 6), 'p50': values[len(values) // 2],
                'max': values[-1], 'total': round(sum(values), 4)}
    return {key: stats(key) for key in ('questions', 'numeric_keys', 'v1_usd', 'v2_usd', 'extract_usd')}


async def model_prices(model: str) -> dict[str, float]:
    """USD per token from the OpenRouter catalogue; flash-lite list prices if unreachable."""
    import httpx

    fallback = {'prompt': 0.30e-6, 'completion': 2.50e-6}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            data = (await client.get('https://openrouter.ai/api/v1/models')).json()['data']
        pricing = next(m['pricing'] for m in data if m['id'] == model)
        return {'prompt': float(pricing['prompt']), 'completion': float(pricing['completion'])}
    except Exception:  # noqa: BLE001 — the estimate falls back to list prices
        return fallback


def _source_hashes() -> dict[str, str]:
    root = Path('src/feap/decision')
    return {p.as_posix(): file_sha256(p) for p in sorted(root.rglob('*.py'))}


def manifest(args: argparse.Namespace, n_cases: int) -> dict:
    prompts = json.dumps([llm.SYSTEM_SINGLE, llm.SYSTEM_BATCH, llm.SYSTEM_EXTRACT,
                          decision_backend.SEMANTIC_OPTIONS, ASSORTMENT_QUESTION.model_dump()],
                         ensure_ascii=False, sort_keys=True)
    return {
        'started_at': datetime.now(UTC).isoformat(timespec='seconds'),
        'git_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'git_diff_sha256': hashlib.sha256(subprocess.check_output(
            ['git', 'diff', 'HEAD', '--', 'src/feap/decision'])).hexdigest(),
        'source_sha256': _source_hashes(),
        'reference_data': database_fingerprint(),
        'cases': {'path': args.cases.as_posix(), 'sha256': file_sha256(args.cases), 'n_run': n_cases},
        'predictions': {'path': args.predictions.as_posix(), 'sha256': file_sha256(args.predictions)},
        'trace': {'path': args.trace.as_posix(), 'sha256': file_sha256(args.trace)} if args.trace else None,
        'holdout_manifest_sha256': file_sha256(HOLDOUT_MANIFEST),
        'arm': args.arm,
        'model': None if args.arm == 'DRY' else args.model,
        'questions_version': QUESTIONS_VERSION,
        'prompts_sha256': hashlib.sha256(prompts.encode()).hexdigest(),
        'thresholds': {'T': decision_backend.T_THRESHOLD, 'F': decision_backend.F_THRESHOLD},
        'cap': args.cap,
        'policies': {name: sorted(f'{s}:{strength or "*"}' for s, strength in members)
                     for name, members in POLICIES.items()},
        'facts': args.facts.as_posix(),
    }


_FROZEN = ('cases', 'predictions', 'trace', 'arm', 'model', 'questions_version', 'prompts_sha256',
           'thresholds', 'cap', 'source_sha256', 'reference_data', 'git_diff_sha256')


def load_facts(path: Path) -> dict[str, dict[str, EvidenceFact | None]]:
    facts: dict[str, dict[str, EvidenceFact | None]] = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                row = json.loads(line)
                facts.setdefault(row['case_id'], {}).update({
                    key: EvidenceFact.model_validate(value) if value else None
                    for key, value in row['facts'].items()})
    return facts


async def run(args: argparse.Namespace) -> None:
    cases = load_cases(args.cases)
    if args.case_id:
        cases = [c for c in cases if c.id in set(args.case_id)]
    holdout = Holdout.load()
    leaks = holdout.pool(cases, 'replay').excluded
    if leaks and not args.allow_holdout:
        raise SystemExit(f'{len(leaks)} cases belong to holdout (e.g. {leaks[0]}); '
                         'the verifier runs on them only at stage 4 (--allow-holdout)')
    predictions = {p.case_id: p for p in load_predictions(args.predictions)}
    todo = runnable(cases, predictions)[:args.limit]
    alternatives = trace_alternatives(args.trace)
    if args.trace is None:
        print('No --trace: alternatives are empty, S3 cannot fire')

    out_dir = args.results_dir or RESULTS_ROOT / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{args.run_id}_verify.jsonl'
    manifest_path = out_dir / f'{args.run_id}_manifest.json'
    args.facts = args.facts or RESULTS_ROOT / 'facts' / f'{args.cases.stem}__{args.predictions.stem}.jsonl'
    args.facts.parent.mkdir(parents=True, exist_ok=True)
    current = manifest(args, len(todo))
    if manifest_path.exists() and not args.no_resume:
        previous = json.loads(manifest_path.read_text(encoding='utf-8'))
        changed = [k for k in _FROZEN if k != 'cases' and previous.get(k) != current.get(k)]
        if previous['cases']['sha256'] != current['cases']['sha256']:
            changed.append('cases')
        if changed:
            raise SystemExit(f'Cannot resume {args.run_id}: changed {changed}; use a new run-id')
    if args.no_resume and out_path.exists():
        out_path.unlink()
    manifest_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    done = set()
    if out_path.exists():
        done = {json.loads(line)['case_id'] for line in out_path.read_text(encoding='utf-8').splitlines() if line}
    todo = [(c, p) for c, p in todo if c.id not in done]

    positions = {p.predicted_code_10[:4] for _, p in todo}
    positions |= {code[:4] for c, _ in todo for code in alternatives.get(c.id, [])}
    with closing(psycopg2.connect(libpq_dsn())) as connection, connection.cursor() as cursor:
        trees: dict[str, PositionRows] = load_positions(cursor, positions)
        headings = load_headings(cursor, {p[:2] for p in positions})

    facts = load_facts(args.facts)
    if args.arm == 'DRY':
        backend, extractor = DryBackend(), None
    else:
        backend = llm.OpenRouterLLM(args.model, batch=args.arm == 'V2', concurrency=args.question_concurrency)
        extractor = llm.OpenRouterExtractor(args.model)
        dry = await asyncio.gather(*(verify_case(
            case_id=c.id, description=c.input.description, chosen_code=p.predicted_code_10,
            alternatives=alternatives.get(c.id, []), trees=trees, backend=DryBackend(), cap=args.cap,
            facts=facts.get(c.id), headings=headings) for c, p in todo))
        cost = estimate(dry, await model_prices(args.model), facts)
        key = 'v1_usd' if args.arm == 'V1' else 'v2_usd'
        need = cost.get(key, {}).get('total', 0) + cost.get('extract_usd', {}).get('total', 0)
        remaining = await openrouter_remaining_usd(llm_api_key())
        print(f'{len(todo)} cases; estimate ${need:.3f} (no prompt cache); balance '
              f'{"unknown" if remaining is None else f"${remaining:.2f}"}')
        if args.max_usd is not None and need > args.max_usd:
            raise SystemExit(f'Estimate ${need:.2f} > --max-usd {args.max_usd}')
        if remaining is not None and need * 1.5 > remaining:
            raise SystemExit(f'Balance ${remaining:.2f} < 1.5 × estimate ${need:.2f}: top up OpenRouter')

    semaphore = asyncio.Semaphore(args.concurrency)
    stop = asyncio.Event()
    written = 0

    async def one(case: CaseV2, pred: CasePrediction) -> None:
        nonlocal written
        if stop.is_set():
            return
        async with semaphore:
            if stop.is_set():
                return
            try:
                trace = await verify_case(
                    case_id=case.id, description=case.input.description, chosen_code=pred.predicted_code_10,
                    alternatives=alternatives.get(case.id, []), trees=trees, backend=backend,
                    extractor=extractor, facts=facts.get(case.id), cap=args.cap, headings=headings)
            except llm.BudgetExhausted as exc:
                stop.set()
                print(f'{case.id}: OpenRouter 402 — stopping; resume after top-up ({exc})')
                return
            except Exception as exc:  # noqa: BLE001 — one broken case must not lose the others
                trace = VerifyTrace(case_id=case.id, arm=args.arm, questions_version=QUESTIONS_VERSION,
                                    chosen_code=pred.predicted_code_10, error=f'{type(exc).__name__}: {exc}')
                print(f'{case.id}: {trace.error}')
        trace.arm = args.arm
        with out_path.open('a', encoding='utf-8') as f:
            f.write(trace.model_dump_json() + '\n')
        # Сбой извлечения не кешируется: следующее плечо попробует снова, а не унаследует U.
        new = {k: v for k, v in trace.facts.items() if k not in (facts.get(case.id) or {})
               and not (v is not None and (v.note or '').startswith(('сбой', 'overflow')))}
        if new and args.arm != 'DRY':
            facts.setdefault(case.id, {}).update(new)
            with args.facts.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'case_id': case.id, 'facts': {
                    k: v.model_dump(mode='json') if v else None for k, v in new.items()}},
                    ensure_ascii=False) + '\n')
        written += 1

    await asyncio.gather(*(one(c, p) for c, p in todo))
    print(f'{written} of {len(todo)} cases → {out_path}')
    if args.arm == 'DRY':
        traces = [VerifyTrace.model_validate_json(line)
                  for line in out_path.read_text(encoding='utf-8').splitlines() if line]
        cost = estimate(traces, await model_prices(args.model))
        summary = {'cases': len(traces), 'errors': [t.case_id for t in traces if t.error], 'estimate': cost}
        (out_dir / f'{args.run_id}_estimate.json').write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=False, indent=2))


def llm_api_key() -> str:
    from feap.config import settings

    return settings.OPENROUTER_API_KEY


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == '__main__':
    main()
