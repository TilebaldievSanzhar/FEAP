"""Score classifier predictions against v2 cases: outcomes and the money they imply.

Works on existing predictions (no LLM calls): a v1 run of the same case ids can
be re-scored. Money needs the database — the product's payment engine prices
every wrong code against the gold one; --no-money skips it.

Run:
    python -m benchmark.scripts.score_v2 \\
        --cases benchmark/data/cases/v2/passport_regression100.jsonl \\
        --predictions benchmark/data/results/v32_baseline/v32_baseline_predictions.jsonl \\
        --run-id v32_baseline --money-date 2026-10-15
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import logging
import subprocess
from collections import Counter
from decimal import Decimal
from pathlib import Path

from benchmark.metrics.consequences import (
    STANDARD_LOT,
    CodeConsequences,
    ConsequenceDiff,
    code_consequences,
    compare_consequences,
)
from benchmark.metrics.outcome import CaseOutcome, Outcome, case_outcome, summarize
from benchmark.metrics.report_v2 import render_markdown
from benchmark.schema.case_v2 import CaseV2, Lot, load_cases
from benchmark.schema.prediction import CasePrediction, load_predictions

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Score predictions against v2 cases')
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--predictions', type=Path, required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--out-dir', type=Path, default=None,
                   help='Default: benchmark/data/results/scored_v2/<run-id>')
    p.add_argument('--money-date', type=dt.date.fromisoformat, default=None,
                   help='Rates and measures for cases without as_of_date (YYYY-MM-DD)')
    p.add_argument('--no-money', action='store_true', help='Outcomes only, no database')
    p.add_argument('--only-predicted', action='store_true',
                   help='Score only cases with a prediction (smoke runs); the report says so')
    return p.parse_args()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def align(cases: list[CaseV2], predictions: list[CasePrediction]) -> dict[str, CasePrediction]:
    rows: dict[str, CasePrediction] = {}
    for pred in predictions:
        if pred.case_id in rows:
            raise ValueError(f'Duplicate prediction for {pred.case_id}; resolve retries first')
        rows[pred.case_id] = pred
    extra = set(rows) - {c.id for c in cases}
    if extra:
        logger.warning('%d predictions have no case and are ignored: %s',
                       len(extra), sorted(extra)[:10])
    return rows


async def price_errors(
    cases: list[CaseV2], outcomes: list[CaseOutcome], *, money_date: dt.date | None,
) -> dict[str, dict]:
    """Consequences of every wrong code on a scored case → {case_id: record}."""
    from feap.db.engine import async_session

    cache: dict[tuple, CodeConsequences] = {}

    async def consequences(code: str, lot: Lot, day: dt.date) -> CodeConsequences:
        key = (code, lot.model_dump_json(), day)
        if key not in cache:
            async with async_session() as session:
                cache[key] = await code_consequences(session, code, lot, rate_date=day)
        return cache[key]

    by_id = {c.id: c for c in cases}
    priced: dict[str, dict] = {}
    for outcome in outcomes:
        if not (outcome.scored and outcome.wrong_code):
            continue
        case = by_id[outcome.case_id]
        day = case.as_of_date or money_date
        if day is None:
            raise SystemExit(f'{case.id} has no as_of_date: pass --money-date')
        lot = case.lot or STANDARD_LOT
        if outcome.gold_digits < 10:
            # A heading-level gold has no payments of its own to compare against.
            priced[case.id] = {'date': day.isoformat(), 'standard_lot': lot.standard,
                               'gold': None, 'predicted': None,
                               'diff': ConsequenceDiff(kind='gold_partial').to_json()}
            continue
        gold = await consequences(outcome.gold_code, lot, day)
        pred = await consequences(outcome.predicted_code, lot, day)
        priced[case.id] = {
            'date': day.isoformat(), 'standard_lot': lot.standard,
            'gold': gold.to_json(), 'predicted': pred.to_json(),
            'diff': compare_consequences(gold, pred).to_json(),
        }
    return priced


def money_summary(outcomes: list[CaseOutcome], priced: dict[str, dict]) -> dict:
    """Totals per outcome: what silent errors would cost, what flags caught."""
    out: dict = {}
    for kind in (Outcome.SILENT_ERROR, Outcome.SAFE_STOP):
        rows = [priced[o.case_id] for o in outcomes if o.case_id in priced and o.outcome is kind]
        deltas = [Decimal(r['diff']['delta_total']) for r in rows
                  if r['diff']['delta_total'] is not None]
        out[kind.value] = {
            'n': len(rows),
            'by_kind': dict(Counter(r['diff']['kind'] for r in rows)),
            'underpaid_cases': sum(d < 0 for d in deltas),
            'underpaid_kgs': str(-sum((d for d in deltas if d < 0), Decimal(0))),
            'overpaid_cases': sum(d > 0 for d in deltas),
            'overpaid_kgs': str(sum((d for d in deltas if d > 0), Decimal(0))),
            'remedies_missed_cases': sum(bool(r['diff']['remedies_missed']) for r in rows),
            'ntm_missed_cases': sum(bool(r['diff']['ntm_missed']) for r in rows),
        }
    return out


def prediction_refdata(predictions_path: Path) -> dict | None:
    """reference_data of the run that produced the predictions, if it was audited."""
    manifest = predictions_path.with_name(
        predictions_path.name.replace('_predictions.jsonl', '_manifest.json'))
    if not manifest.exists():
        return None
    return json.loads(manifest.read_text(encoding='utf-8')).get('reference_data')


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(levelname)-8s %(message)s')
    args = parse_args()
    out_dir = args.out_dir or Path('benchmark/data/results/scored_v2') / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    cases = load_cases(args.cases)
    predictions = align(cases, load_predictions(args.predictions))
    if args.only_predicted:
        planned = len(cases)
        cases = [c for c in cases if c.id in predictions]
        logger.warning('Partial run: scoring %d of %d cases', len(cases), planned)
    outcomes = [case_outcome(c, predictions.get(c.id)) for c in cases]
    summary = summarize(outcomes)
    if args.only_predicted:
        summary['partial'] = {'scored_cases': len(cases), 'planned_cases': planned}

    priced: dict[str, dict] = {}
    manifest: dict = {
        'scored_at': dt.datetime.now(dt.UTC).isoformat(),
        'run_id': args.run_id,
        'git_revision': subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True,
                                       text=True).stdout.strip() or None,
        'cases': {'path': args.cases.as_posix(), 'sha256': _sha256(args.cases)},
        'predictions': {'path': args.predictions.as_posix(),
                        'sha256': _sha256(args.predictions),
                        'reference_data': prediction_refdata(args.predictions)},
    }
    if not args.no_money:
        from benchmark.evaluator.fingerprint import database_fingerprint, database_status
        from feap.config import settings

        priced = asyncio.run(price_errors(cases, outcomes, money_date=args.money_date))
        summary['money'] = money_summary(outcomes, priced)
        days = {dt.date.fromisoformat(r['date']) for r in priced.values()}
        manifest['money'] = {
            'date': args.money_date.isoformat() if args.money_date else None,
            'standard_lot': json.loads(STANDARD_LOT.model_dump_json()),
            'settings': {k: getattr(settings, k) for k in (
                'DT_VAT_INCLUDE_REMEDY_DUTIES', 'DT_CUSTOMS_FEE_MODE', 'REFDATA_GUARD')},
        }
        manifest['reference_data'] = database_fingerprint(payments=True, fx_dates=days)
        manifest['refdata_status'] = database_status()
    summary['run_id'] = args.run_id
    summary['cases_path'] = args.cases.as_posix()
    summary['predictions_path'] = args.predictions.as_posix()

    prefix = out_dir / args.run_id
    with prefix.with_name(prefix.name + '_v2_cases.jsonl').open('w', encoding='utf-8') as f:
        for outcome in outcomes:
            f.write(json.dumps({**outcome.to_json(), 'money': priced.get(outcome.case_id)},
                               ensure_ascii=False) + '\n')
    prefix.with_name(prefix.name + '_v2_summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    prefix.with_name(prefix.name + '_v2_manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report = render_markdown(summary, outcomes, priced, manifest)
    report_path = prefix.with_name(prefix.name + '_v2_report.md')
    report_path.write_text(report, encoding='utf-8')
    print(report)
    print(f'\n→ {report_path}')


if __name__ == '__main__':
    main()
