"""Navigation, stage 2: choose the heading from the cascade's candidates and precedents.

R0 showed that the cascade usually had the right heading in view and picked
another one (docs/navigation_preregistration.md). This script measures selectors
over one candidate pool per case:

- candidates: headings the cascade considered (L3 shortlist and candidates, its own
  choice) and headings of the ``--k`` most similar precedent decisions, in code order;
- ``--arm CASCADE``: the cascade's heading (control, no calls);
- ``--arm PREC1``: the heading of the most similar precedent (no calls);
- ``--arm FL``: flash-lite, one structured call with heading texts and precedents;
- ``--arm DM --model typesafe/jev-1.14``: a decision model, one choice question.

Precedents come from the calibration pool without the evaluated cases and their
near-duplicates (``benchmark.precedents``); holdout sets are refused.

Run:
    python -m benchmark.scripts.heading_select --arm DM --model typesafe/jev-1.14 \\
        --run-id select_jev_dev150 --cases benchmark/data/cases/v2/eaeu_pd_dev150.jsonl \\
        --predictions benchmark/data/results/r0_v32_dev150/r0_v32_dev150_predictions.jsonl \\
        --trace benchmark/data/results/r0_v32_dev150/r0_v32_dev150_trace.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import time
from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg2

from benchmark.evaluator.fingerprint import database_fingerprint, libpq_dsn
from benchmark.holdout import Holdout, file_sha256
from benchmark.metrics.outcome import case_outcome
from benchmark.metrics.stats import share
from benchmark.precedents import Precedent, PrecedentIndex
from benchmark.schema.case_v2 import CaseV2, load_cases
from benchmark.schema.prediction import load_predictions

RESULTS_ROOT = Path('benchmark/data/results/navigation')
POOL = Path('benchmark/data/cases/v2/pools/eaeu_pd_calibration.jsonl')
DECISIONS_URL = 'https://openrouter.ai/api/alpha/decisions'
FLASH_LITE = 'google/gemini-3.5-flash-lite'
SEED = 20261105
EVIDENCE = 5          # precedents shown to the selector
EVIDENCE_CHARS = 400  # characters of each precedent description

INSTRUCTIONS = (
    'Выбери товарную позицию ТН ВЭД ЕАЭС (4 знака) для товара из описания. Решают тексты товарных позиций '
    '(ОПИ 1): более конкретное описание предпочтительнее общего (ОПИ 3а); специальная позиция для вида изделия '
    'предпочтительнее позиции по материалу. Похожие решения таможенных органов — подсказка, а не правило: '
    'другой товар может отличаться существенным признаком.'
)
SYSTEM_FL = (
    'Ты — эксперт по классификации товаров по ТН ВЭД ЕАЭС. ' + INSTRUCTIONS
    + ' Ответь JSON: сначала короткий анализ (до трёх предложений), затем код выбранной позиции из списка.'
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--predictions', type=Path, required=True)
    p.add_argument('--trace', type=Path, required=True)
    p.add_argument('--arm', choices=['CASCADE', 'PREC1', 'FL', 'DM'], required=True)
    p.add_argument('--model')
    p.add_argument('--run-id', required=True)
    p.add_argument('--k', type=int, default=10, help='Precedents whose headings join the candidates')
    p.add_argument('--concurrency', type=int, default=4)
    p.add_argument('--no-resume', action='store_true')
    return p.parse_args()


def cascade_candidates(trace: Path) -> dict[str, set[str]]:
    found: dict[str, set[str]] = defaultdict(set)
    with trace.open(encoding='utf-8') as f:
        for line in f:
            record = json.loads(line)
            name = ((record.get('response_format') or {}).get('json_schema') or {}).get('name')
            if name not in ('ShortlistResult', 'PositionResult'):
                continue
            try:
                data = json.loads(record.get('raw_content') or '')
            except json.JSONDecodeError:
                continue
            items = data.get('shortlisted') if name == 'ShortlistResult' else [
                c.get('position') for c in data.get('candidates') or [] if isinstance(c, dict)]
            for item in items or []:
                code = re.sub(r'\D', '', str(item or ''))
                if len(code) >= 4:
                    found[record['case_id']].add(code[:4])
    return found


def evidence(precedents: list[Precedent]) -> list[dict]:
    return [{'описание': p.description[:EVIDENCE_CHARS], 'код': p.code} for p in precedents[:EVIDENCE]]


async def select_fl(client: httpx.AsyncClient, key: str, case: CaseV2, candidates: dict[str, str],
                    precedents: list[Precedent]) -> dict:
    user = json.dumps({'товар': case.input.description, 'похожие решения': evidence(precedents),
                       'товарные позиции': candidates}, ensure_ascii=False)
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['analysis', 'heading'],
              'properties': {'analysis': {'type': 'string'},
                             'heading': {'type': 'string', 'enum': list(candidates)}}}
    body = {'model': FLASH_LITE, 'temperature': 0, 'max_tokens': 1024,
            'messages': [{'role': 'system', 'content': SYSTEM_FL}, {'role': 'user', 'content': user}],
            'response_format': {'type': 'json_schema',
                                'json_schema': {'name': 'HeadingChoice', 'strict': True, 'schema': schema}},
            'provider': {'sort': 'price', 'require_parameters': True}}
    response = await client.post('https://openrouter.ai/api/v1/chat/completions', json=body,
                                 headers={'Authorization': f'Bearer {key}'})
    response.raise_for_status()
    data = response.json()
    content = json.loads(data['choices'][0]['message']['content'])
    return {'heading': content['heading'], 'analysis': content['analysis'],
            'cost_usd': float((data.get('usage') or {}).get('cost') or 0), 'revision': data.get('model')}


async def select_dm(client: httpx.AsyncClient, key: str, model: str, case: CaseV2, candidates: dict[str, str],
                    precedents: list[Precedent]) -> dict:
    body = {'model': model,
            'state': {'товар': case.input.description, 'похожие решения': evidence(precedents)},
            'questions': {'heading': {'type': 'choice', 'instructions': INSTRUCTIONS, 'criteria': candidates}}}
    response = await client.post(DECISIONS_URL, json=body, headers={'Authorization': f'Bearer {key}'})
    response.raise_for_status()
    data = response.json()
    answer = data['answers']['heading']
    if answer.get('type') != 'choice':
        return {'heading': None, 'error': f'answer {answer.get("type")}', 'revision': data.get('model'),
                'cost_usd': float((data.get('usage') or {}).get('cost') or 0)}
    return {'heading': answer['choice'], 'probabilities': answer.get('probabilities'),
            'cost_usd': float((data.get('usage') or {}).get('cost') or 0), 'revision': data.get('model')}


def bootstrap(pairs: list[tuple[bool, bool]]) -> list[float]:
    rng = random.Random(SEED)
    n = len(pairs)
    diffs = sorted(sum(a - b for a, b in (pairs[rng.randrange(n)] for _ in range(n))) / n for _ in range(10_000))
    return [round(diffs[250], 4), round(diffs[9749], 4)]


async def run(args: argparse.Namespace) -> None:
    holdout = Holdout.load()
    cases = load_cases(args.cases)
    if holdout.pool(cases, 'replay').excluded:
        raise SystemExit('Holdout cases are not used for navigation experiments')
    cases = holdout.pool(cases, 'calibration').cases
    predictions = {p.case_id: p for p in load_predictions(args.predictions)}
    own = cascade_candidates(args.trace)
    index = PrecedentIndex(load_cases(POOL), exclude=cases)
    out_dir = RESULTS_ROOT / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{args.run_id}_select.jsonl'
    if args.no_resume and out_path.exists():
        out_path.unlink()
    done = {json.loads(line)['case_id'] for line in out_path.read_text(encoding='utf-8').splitlines()
            if line} if out_path.exists() else set()

    pools: dict[str, tuple[list[str], list[Precedent]]] = {}
    for case in cases:
        pred = predictions.get(case.id)
        precedents = index.search(case.input.description, args.k)
        heads = set(own.get(case.id, set())) | {p.code[:4] for p in precedents}
        if pred and pred.predicted_code_10:
            heads.add(pred.predicted_code_10[:4])
        pools[case.id] = (sorted(heads), precedents)
    with closing(psycopg2.connect(libpq_dsn())) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT code, name FROM tnved_tree WHERE length(code) = 4')
        names = dict(cursor.fetchall())

    manifest = {
        'started_at': datetime.now(UTC).isoformat(timespec='seconds'), 'arm': args.arm, 'model': args.model,
        'k': args.k, 'evidence': EVIDENCE, 'instructions': INSTRUCTIONS,
        'cases': {'path': args.cases.as_posix(), 'sha256': file_sha256(args.cases), 'n': len(cases)},
        'predictions_sha256': file_sha256(args.predictions), 'pool_sha256': file_sha256(POOL),
        'index': {'size': len(index.cases), 'dropped': dict(index.dropped)},
        'reference_data': database_fingerprint(),
    }
    (out_dir / f'{args.run_id}_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                                          encoding='utf-8')
    from feap.config import settings

    key = settings.OPENROUTER_API_KEY
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(client: httpx.AsyncClient, case: CaseV2) -> None:
        heads, precedents = pools[case.id]
        pred = predictions.get(case.id)
        row = {'case_id': case.id, 'gold': case.gold.classification.code, 'candidates': heads,
               'cascade': pred.predicted_code_10[:4] if pred and pred.predicted_code_10 else None,
               'precedents': [p.code for p in precedents]}
        started = time.perf_counter()
        if args.arm == 'CASCADE':
            row['heading'] = row['cascade']
        elif args.arm == 'PREC1':
            row['heading'] = precedents[0].code[:4] if precedents else None
        else:
            candidates = {code: names.get(code, code) for code in heads}
            async with semaphore:
                for attempt in (1, 2):
                    try:
                        result = (await select_fl(client, key, case, candidates, precedents) if args.arm == 'FL'
                                  else await select_dm(client, key, args.model, case, candidates, precedents))
                        break
                    except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
                        if '402' in str(exc):
                            raise
                        result = {'heading': None, 'error': f'{type(exc).__name__}: {exc}'[:300]}
            row.update(result)
        row['latency_ms'] = round((time.perf_counter() - started) * 1000, 1)
        with out_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')

    async with httpx.AsyncClient(timeout=120) as client:
        await asyncio.gather(*(one(client, c) for c in cases if c.id not in done))

    rows = {r['case_id']: r for r in map(json.loads, out_path.read_text(encoding='utf-8').splitlines()) if r}
    scored = [c for c in cases if c.id in rows and case_outcome(c, predictions.get(c.id)).scored]
    right = [rows[c.id]['heading'] == c.gold.classification.code[:4] for c in scored]
    base = [rows[c.id]['cascade'] == c.gold.classification.code[:4] for c in scored]
    covered = [c.gold.classification.code[:4] in rows[c.id]['candidates'] for c in scored]
    summary = {
        'arm': args.arm, 'model': args.model, 'n': len(scored),
        'heading_accuracy': share(sum(right), len(scored)),
        'cascade_heading_accuracy': share(sum(base), len(scored)),
        'minus_cascade': {'diff': round((sum(right) - sum(base)) / len(scored), 4),
                          'ci95': bootstrap(list(zip(right, base)))},
        'fixed': sum(r and not b for r, b in zip(right, base)), 'broken': sum(b and not r for r, b in zip(right, base)),
        'gold_in_candidates': share(sum(covered), len(scored)),
        'candidates_mean': round(sum(len(rows[c.id]['candidates']) for c in scored) / len(scored), 2),
        'cost_usd': round(sum(rows[c.id].get('cost_usd') or 0 for c in scored), 4),
        'errors': sum(1 for c in scored if rows[c.id].get('error')),
        'latency_ms_p50': sorted(rows[c.id]['latency_ms'] for c in scored)[len(scored) // 2],
    }
    (out_dir / f'{args.run_id}_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n',
                                                         encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == '__main__':
    main()
