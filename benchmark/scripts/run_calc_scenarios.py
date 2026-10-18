"""Run the payment scenarios on the current reference data and compare with the oracle.

No LLM. Needs the database. Exit code 1 if any scenario fails.

Run:
    python -m benchmark.scripts.run_calc_scenarios --run-id calc_2026-10-16
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

from benchmark.metrics.payments_oracle import expected_payments
from benchmark.schema.calc_scenario import CalcScenario, load_scenarios

SCENARIOS_PATH = Path('benchmark/data/scenarios/calc_v1.jsonl')


def actual_payments(out) -> tuple[dict[str, Decimal | None], dict[str, str]]:
    """Engine lines by kind (remedy lines of one kind are summed) and their rate texts."""
    amounts: dict[str, Decimal | None] = {}
    rates: dict[str, str] = {}
    for line in out.payments:
        rates.setdefault(line.kind, line.rate)
        if line.amount is None or amounts.get(line.kind, Decimal(0)) is None:
            amounts[line.kind] = None
        else:
            amounts[line.kind] = amounts.get(line.kind, Decimal(0)) + line.amount
    amounts['customs_value'] = out.customs_value
    return amounts, rates


def check(scenario: CalcScenario, out) -> dict:
    """Verdict for one scenario: lines compared to the tyiyn, mismatches attributed."""
    amounts, rates = actual_payments(out)
    problems: list[dict] = []
    if out.status != scenario.expected_status:
        problems.append({'what': 'status', 'expected': scenario.expected_status,
                         'actual': out.status, 'cause': 'engine or reference data'})
    for needle in scenario.expected_missing:
        if not any(needle in m for m in out.missing_parameters):
            problems.append({'what': 'missing', 'expected': needle,
                             'actual': out.missing_parameters, 'cause': 'engine'})
    lines = []
    if scenario.declared is not None:
        if rates.get('duty') != scenario.declared.duty.text:
            problems.append({'what': 'duty rate', 'expected': scenario.declared.duty.text,
                             'actual': rates.get('duty'), 'cause': 'reference data'})
        for kind, expected in expected_payments(scenario.calc, scenario.declared).items():
            actual = amounts.get(kind)
            lines.append({'kind': kind, 'expected': str(expected),
                          'actual': None if actual is None else str(actual),
                          'ok': actual == expected})
            if actual != expected:
                rate_differs = kind == 'duty' and rates.get('duty') != scenario.declared.duty.text
                problems.append({'what': kind, 'expected': str(expected),
                                 'actual': None if actual is None else str(actual),
                                 'cause': 'reference data' if rate_differs else 'engine'})
        extra = sorted(set(amounts) - {line['kind'] for line in lines})
        for kind in extra:
            problems.append({'what': kind, 'expected': 'no such payment',
                             'actual': str(amounts[kind]), 'cause': 'engine or reference data'})
    return {
        'id': scenario.id, 'title': scenario.title, 'verification': scenario.verification,
        'ok': not problems, 'status': out.status, 'lines': lines, 'problems': problems,
        'missing_parameters': out.missing_parameters,
    }


async def run_all(scenarios: list[CalcScenario]) -> list[dict]:
    from feap.calc.engine import run_calc
    from feap.db.engine import async_session

    results = []
    for scenario in scenarios:
        async with async_session() as session:
            out = await run_calc(session, scenario.calc)
        results.append(check(scenario, out))
    return results


def render(results: list[dict], manifest: dict) -> str:
    passed = sum(r['ok'] for r in results)
    lines = [
        f"# Сценарии расчёта платежей — {manifest['run_id']}", '',
        f"Сценарии: `{manifest['scenarios']['path']}` (sha256 "
        f"{manifest['scenarios']['sha256'][:12]}…). Пройдено: **{passed} из {len(results)}**.",
        'Ожидаемые суммы считает независимый оракул по заявленным ставкам; фактические — '
        '`calc.run_calc` на текущих справочниках.', '',
        '| Сценарий | Проверка | Источник ставок | Итог |', '|---|---|---|---|',
    ]
    for r in results:
        verdict = '✅' if r['ok'] else '❌ ' + '; '.join(
            f"{p['what']}: ждали {p['expected']}, получили {p['actual']} ({p['cause']})"
            for p in r['problems'])
        lines.append(f"| {r['id']} | {r['title']} | {r['verification']} | {verdict} |")
    lines += ['', '## Суммы, сом', '', '| Сценарий | Платёж | Оракул | Движок |', '|---|---|---:|---:|']
    for r in results:
        for line in r['lines']:
            mark = '' if line['ok'] else ' ❌'
            lines.append(f"| {r['id']} | {line['kind']} | {line['expected']} | "
                         f"{line['actual']}{mark} |")
    return '\n'.join(lines) + '\n'


def main() -> None:
    p = argparse.ArgumentParser(description='Run payment scenarios against the oracle')
    p.add_argument('--scenarios', type=Path, default=SCENARIOS_PATH)
    p.add_argument('--run-id', default=f'calc_{dt.date.today().isoformat()}')
    p.add_argument('--out-dir', type=Path,
                   default=Path('benchmark/data/results/scored_v2/calc'))
    args = p.parse_args()

    from benchmark.evaluator.fingerprint import database_fingerprint, database_status
    from feap.config import settings

    scenarios = load_scenarios(args.scenarios)
    results = asyncio.run(run_all(scenarios))
    days = {s.calc.rate_date for s in scenarios if s.calc.rate_date}
    manifest = {
        'run_id': args.run_id,
        'ran_at': dt.datetime.now(dt.UTC).isoformat(),
        'scenarios': {'path': args.scenarios.as_posix(),
                      'sha256': hashlib.sha256(args.scenarios.read_bytes()).hexdigest()},
        'settings': {k: getattr(settings, k) for k in (
            'DT_VAT_INCLUDE_REMEDY_DUTIES', 'DT_CUSTOMS_FEE_MODE', 'DT_CALC_INDEX_KGS',
            'VAT_RATE_DEFAULT', 'REFDATA_GUARD')},
        'reference_data': database_fingerprint(payments=True, fx_dates=days),
        'refdata_status': database_status(),
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.out_dir / args.run_id
    prefix.with_name(prefix.name + '_results.json').write_text(
        json.dumps({'manifest': manifest, 'results': results}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')
    report = render(results, manifest)
    prefix.with_name(prefix.name + '_report.md').write_text(report, encoding='utf-8')
    print(report)
    sys.exit(0 if all(r['ok'] for r in results) else 1)


if __name__ == '__main__':
    main()
