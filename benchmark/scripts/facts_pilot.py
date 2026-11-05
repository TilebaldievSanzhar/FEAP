"""Facts engine pilot, phases A and B (docs/decision_facts_preregistration.md).

Decision models answer the fact bank (``feap.decision.facts``, facts-1) about registry
descriptions; routers built on the answers rank tariff groups. Subcommands:

- ``split``   — eval = dev148 + fresh145 (cases with cascade outputs); training pool = the
  calibration pool without the eval records, their ten-digit codes and near-duplicates;
  train_d1 = the whole training pool in random order, train_jev and silver are its prefixes;
- ``collect`` — ask one model about one set; resumable JSONL, cost estimate and a cap;
- ``score``   — phase A (bank: unknown, agreement, silver accuracy, information) and
  phase B (routers against the cascade and BM25), gates of the pre-registration.

Run:
    python -m benchmark.scripts.facts_pilot split
    python -m benchmark.scripts.facts_pilot collect --set smoke --model liquid/d1.1 --max-usd 0.01
    python -m benchmark.scripts.facts_pilot collect --set train_d1 --model liquid/d1.1 --max-usd 1.5
    python -m benchmark.scripts.facts_pilot score
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

from benchmark.holdout import NEAR_DUPLICATE, Holdout, file_sha256, load_pool, shingles, source_of
from benchmark.metrics.stats import share
from benchmark.precedents import PrecedentIndex
from benchmark.schema.case_v2 import CaseV2, load_cases
from benchmark.schema.prediction import load_predictions
from feap.decision.facts import UNKNOWN, FactBank, Facts, ask_facts, load_bank, top_label
from feap.decision.facts.router import NaiveBayes, Profiles, combine, load_profiles, profile_scores, rank
from feap.decision.providers.openrouter import BudgetExhausted
from feap.decision.providers.openrouter_decisions import OpenRouterDecisions

CASES = Path('benchmark/data/cases/v2')
POOL = CASES / 'pools/eaeu_pd_calibration.jsonl'
DEV = CASES / 'eaeu_pd_dev150.jsonl'
FRESH = CASES / 'eaeu_pd_fresh150.jsonl'
SPLIT = Path('benchmark/data/decision/facts_pilot_split.json')
SILVER = Path('benchmark/data/decision/facts_silver_v1.jsonl')
RESULTS = Path('benchmark/data/results/facts')
CASCADE = {
    'dev': Path('benchmark/data/results/r0_v32_dev150/r0_v32_dev150_predictions.jsonl'),
    'fresh': Path('benchmark/data/results/e_n3_c0_fresh150/e_n3_c0_fresh150_predictions.jsonl'),
    'fresh_c1': Path('benchmark/data/results/e_n3_c1_fresh150/e_n3_c1_fresh150_predictions.jsonl'),
}
SEED = 20261107
BOOTSTRAP_SEED = 20261105
SIZES = {'train_jev': 1000, 'silver': 80, 'smoke': 3}  # train_d1 — весь обучающий пул
PRIMARY = 'liquid/d1.1'
SECOND = 'typesafe/jev-1.14'
SHOOTOUT = ['microsoft/microsoft-decision-1', 'cloudflare/clef-flash', 'upstage/solar-decide',
            'inception/mercury-decide', 'openai/gpt-6-luna-decisions']
#: $ per 1M input tokens (OpenRouter endpoints, 6 Nov 2026) and tokens per character of
#: Russian text measured on stage-1 calls (state is billed with every question).
PRICE = {'liquid/d1.1': 0.02, 'typesafe/jev-1.14': 0.042, 'microsoft/microsoft-decision-1': 0.042,
         'cloudflare/clef': 0.192, 'cloudflare/clef-flash': 0.018, 'upstage/solar-decide': 0.05,
         'inception/mercury-decide': 0.02, 'openai/gpt-6-luna-decisions': 0.10}
TOKENS_PER_CHAR = {'liquid/d1.1': 0.314, 'typesafe/jev-1.14': 0.618}
DEFAULT_TOKENS_PER_CHAR = 0.62
EPS, ALPHA = 0.05, 1.0


# ── split ─────────────────────────────────────────────────────────────────────

def eval_sets() -> dict[str, list[CaseV2]]:
    return {'dev': Holdout.load().pool(load_cases(DEV), 'calibration').cases, 'fresh': load_cases(FRESH)}


def build_split() -> None:
    evals = eval_sets()
    evaluated = [c for cases in evals.values() for c in cases]
    sources = {source_of(c) for c in evaluated}
    codes = {c.gold.classification.code for c in evaluated}
    grams = [shingles(c.input.description) for c in evaluated]
    pool = load_pool(POOL, 'calibration')
    kept, dropped = [], Counter()
    for case in pool:
        if source_of(case) in sources:
            dropped['record'] += 1
        elif case.gold.classification.code in codes:
            dropped['code'] += 1
        elif any(g & o and len(g & o) / len(g | o) >= NEAR_DUPLICATE
                 for g in [shingles(case.input.description)] for o in grams):
            dropped['near_duplicate'] += 1
        else:
            kept.append(case)
    order = random.Random(SEED).sample([c.id for c in sorted(kept, key=lambda c: c.id)], len(kept))
    sets = {'train_d1': order, 'train_jev': order[:SIZES['train_jev']], 'silver': order[:SIZES['silver']],
            'smoke': order[-SIZES['smoke']:]}
    split = {
        'seed': SEED, 'pool': {'path': POOL.as_posix(), 'sha256': file_sha256(POOL), 'n': len(pool)},
        'eval': {name: [c.id for c in cases] for name, cases in evals.items()},
        'eval_sources': {name: {'path': p.as_posix(), 'sha256': file_sha256(p)} for name, p in (('dev', DEV), ('fresh', FRESH))},
        'training_pool': {'n': len(kept), 'dropped': dict(dropped)},
        'sets': sets,
    }
    SPLIT.parent.mkdir(parents=True, exist_ok=True)
    SPLIT.write_text(json.dumps(split, ensure_ascii=False, indent=1) + '\n', encoding='utf-8', newline='\n')
    print(f"eval: dev {len(evals['dev'])}, fresh {len(evals['fresh'])}; training pool {len(kept)} of {len(pool)} "
          f"(dropped {dict(dropped)}); sets: {({k: len(v) for k, v in sets.items()})} → {SPLIT}")


def load_split() -> dict:
    return json.loads(SPLIT.read_text(encoding='utf-8'))


def cases_by_id() -> dict[str, CaseV2]:
    out = {c.id: c for c in load_pool(POOL, 'calibration')}
    for cases in eval_sets().values():
        out.update({c.id: c for c in cases})
    return out


def set_ids(split: dict, name: str) -> list[str]:
    if name == 'eval':
        return split['eval']['dev'] + split['eval']['fresh']
    return split['sets'][name]


# ── collect ───────────────────────────────────────────────────────────────────

def slug(model: str) -> str:
    return model.replace('/', '__')


def facts_path(bank: FactBank, set_name: str, model: str) -> Path:
    return RESULTS / bank.version / f'{set_name}__{slug(model)}.jsonl'


def estimate_usd(bank: FactBank, model: str, descriptions: list[str]) -> float:
    block = sum(len(bank.instructions_for(q)) + sum(len(k) + len(v) for k, v in q.options.items())
                for q in bank.decision_questions())
    chars = sum(len(bank.questions) * len(d) + block for d in descriptions)
    return chars * TOKENS_PER_CHAR.get(model, DEFAULT_TOKENS_PER_CHAR) * PRICE[model] / 1e6


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


async def collect(args: argparse.Namespace) -> None:
    bank = load_bank()
    split = load_split()
    cases = cases_by_id()
    out = facts_path(bank, args.set, args.model)
    done = {r['case_id'] for r in read_jsonl(out) if not r.get('error')}
    todo = [cid for cid in set_ids(split, args.set) if cid not in done]
    if args.max_cases:
        todo = todo[:args.max_cases]
    estimate = estimate_usd(bank, args.model, [cases[c].input.description for c in todo])
    print(f'{args.model} on {args.set}: {len(todo)} to ask ({len(done)} done), estimate ${estimate:.3f}')
    if estimate > args.max_usd:
        raise SystemExit(f'estimate ${estimate:.3f} > --max-usd {args.max_usd}')
    if not todo:
        return
    executor = OpenRouterDecisions(args.model, concurrency=args.concurrency, instructions=bank.instructions_for)
    out.parent.mkdir(parents=True, exist_ok=True)
    spent, failed, written = 0.0, 0, 0
    lock = asyncio.Lock()
    queue = asyncio.Queue()
    for cid in todo:
        queue.put_nowait(cid)
    stop = asyncio.Event()

    async def worker() -> None:
        nonlocal spent, failed, written
        while not queue.empty() and not stop.is_set():
            cid = queue.get_nowait()
            started = time.perf_counter()
            try:
                facts, answers, calls = await ask_facts(executor, bank, cases[cid].input.description)
            except BudgetExhausted as exc:
                print(f'stopped: {exc}')
                stop.set()
                return
            errors = [c.error for c in calls if c.error] or [a.error for a in answers if a.status != 'ok']
            row = {
                'case_id': cid, 'set': args.set, 'model': args.model,
                'revision': next((c.revision for c in calls if c.revision), None),
                'bank': bank.version, 'bank_sha256': bank.sha256, 'facts': facts,
                'answers': [{'q': a.question_id, 'status': a.status, 'label': a.label,
                             'distribution': a.distribution, 'error': a.error} for a in answers],
                'cost_usd': round(sum(c.cost_usd or 0 for c in calls), 7),
                'latency_ms': round((time.perf_counter() - started) * 1000),
                'error': '; '.join(e for e in errors if e)[:300] if all(a.status != 'ok' for a in answers) else None,
            }
            async with lock:
                with out.open('a', encoding='utf-8', newline='\n') as f:
                    f.write(json.dumps(row, ensure_ascii=False) + '\n')
                spent += row['cost_usd']
                failed += bool(row['error'])
                written += 1
                if written % 100 == 0:
                    print(f'  {written}/{len(todo)} ${spent:.3f}, failed {failed}')
                if spent > args.max_usd:
                    print(f'stopped: spent ${spent:.3f} > --max-usd {args.max_usd}')
                    stop.set()

    await asyncio.gather(*(worker() for _ in range(args.concurrency)))
    print(f'done: {written} written, ${spent:.4f}, failed {failed} → {out}')


# ── score ─────────────────────────────────────────────────────────────────────

def load_facts(bank: FactBank, set_name: str, model: str) -> dict[str, dict]:
    rows = {}
    for r in read_jsonl(facts_path(bank, set_name, model)):
        if r.get('bank_sha256') != bank.sha256:
            raise SystemExit(f'{set_name}/{model}: answers for another bank file')
        rows[r['case_id']] = r  # the last answer wins after a re-ask
    return rows


def group_of(case: CaseV2) -> str:
    return case.gold.classification.code[:2]


def bootstrap(values: list[bool]) -> list[float] | None:
    if not values:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(10_000))
    return [round(means[250], 4), round(means[9749], 4)]


def pct(x: float | None) -> str:
    return '—' if x is None else f'{x:.0%}'


def phase_a(bank: FactBank, split: dict, cases: dict[str, CaseV2]) -> dict:
    d1 = load_facts(bank, 'train_d1', PRIMARY)
    jev = load_facts(bank, 'train_jev', SECOND)
    silver = {r['case_id']: r['labels'] for r in read_jsonl(SILVER)}
    shootout = {m: load_facts(bank, 'silver', m) for m in SHOOTOUT}
    executors = {PRIMARY: d1, SECOND: jev, **shootout}
    out = {'questions': {}, 'silver_n': len(silver)}
    groups = {cid: group_of(cases[cid]) for cid in d1}
    for q in bank.questions:
        row = {'conditional': q.absent is not None}
        for name, rows, ids in ((PRIMARY, d1, split['sets']['train_d1']), (SECOND, jev, split['sets']['train_jev'])):
            tops = [top_label(rows[c]['facts'][q.id]) for c in ids if c in rows]
            row[f'unknown_{name}'] = share(sum(t == UNKNOWN for t in tops), len(tops))
            row[f'failed_{name}'] = share(sum(any(a['q'] == q.id and a['status'] != 'ok' for a in rows[c]['answers'])
                                              for c in ids if c in rows), len(tops))
        both = [c for c in split['sets']['train_jev'] if c in d1 and c in jev]
        row['agreement'] = share(sum(top_label(d1[c]['facts'][q.id]) == top_label(jev[c]['facts'][q.id]) for c in both),
                                 len(both))
        for name, rows in executors.items():
            ids = [c for c in silver if c in rows and q.id in silver[c]]
            tops = {c: top_label(rows[c]['facts'][q.id]) for c in ids}
            applicable = [c for c in ids if silver[c][q.id] != q.absent]
            decisive = [c for c in ids if tops[c] != UNKNOWN]
            row[f'silver_{name}'] = {
                'accuracy': share(sum(tops[c] == silver[c][q.id] for c in ids), len(ids)),
                'applicable_accuracy': share(sum(tops[c] == silver[c][q.id] for c in applicable), len(applicable))
                if q.absent else None,
                'precision': share(sum(tops[c] == silver[c][q.id] for c in decisive), len(decisive)),
            }
        # information about the gold group in the primary executor's answer (bits)
        joint = Counter((top_label(d1[c]['facts'][q.id]), groups[c]) for c in d1)
        n = sum(joint.values())
        pa, pg = Counter(), Counter()
        for (a, g), k in joint.items():
            pa[a] += k
            pg[g] += k
        row['mutual_information_bits'] = round(sum(k / n * math.log2(k * n / (pa[a] * pg[g]))
                                                   for (a, g), k in joint.items()), 3) if n else None
        out['questions'][q.id] = row
    core = [q for q in bank.questions if q.absent is None]
    passed = [q.id for q in core
              if (out['questions'][q.id][f'unknown_{PRIMARY}']['share'] or 0) <= 0.15
              and (out['questions'][q.id][f'silver_{PRIMARY}']['accuracy']['share'] or 0) >= 0.80]
    out['G3'] = {'core_questions': len(core), 'passed': passed, 'ok': len(passed) >= 8}
    shoot = {}
    for name, rows in executors.items():
        pairs = [(top_label(rows[c]['facts'][q.id]), silver[c][q.id]) for c in silver if c in rows
                 for q in core if q.id in silver[c]]
        cost = [rows[c]['cost_usd'] for c in silver if c in rows]
        shoot[name] = {'n_cases': len(cost), 'core_accuracy': share(sum(a == b for a, b in pairs), len(pairs)),
                       'core_unknown': share(sum(a == UNKNOWN for a, _ in pairs), len(pairs)),
                       'usd_per_case': round(sum(cost) / len(cost), 6) if cost else None}
    out['executors_on_silver'] = shoot
    return out


def bm25_scores(index: PrecedentIndex, text: str, k: int = 10) -> dict[str, float]:
    votes: dict[str, float] = defaultdict(float)
    for p in index.search(text, k):
        votes[p.code[:2]] += p.score
    return dict(votes)


def router_metrics(rankings: dict[str, list[str]], evaluated: list[CaseV2], cascade: dict[str, str | None]) -> dict:
    def gold_rank(c):
        r = rankings.get(c.id) or []
        return r.index(group_of(c)) if group_of(c) in r else math.inf

    def cascade_rank(c):
        r = rankings.get(c.id) or []
        g = cascade.get(c.id)
        return r.index(g) if g in r else math.inf

    top1 = [gold_rank(c) < 1 for c in evaluated]
    top3 = [gold_rank(c) < 3 for c in evaluated]
    errors = [c for c in evaluated if cascade.get(c.id) != group_of(c)]
    right = [c for c in evaluated if cascade.get(c.id) == group_of(c)]
    rescue3 = [gold_rank(c) < 3 for c in errors]
    disagree = [c for c in evaluated if (rankings.get(c.id) or [None])[0] != cascade.get(c.id)]
    return {
        'n': len(evaluated), 'top1': share(sum(top1), len(top1)), 'top3': share(sum(top3), len(top3)),
        'top3_ci95': bootstrap(top3),
        'cascade_errors': len(errors),
        'rescue1': share(sum(gold_rank(c) < 1 for c in errors), len(errors)),
        'rescue3': share(sum(rescue3), len(errors)), 'rescue3_ci95': bootstrap(rescue3),
        'gold_above_cascade': share(sum(gold_rank(c) < cascade_rank(c) for c in errors), len(errors)),
        'damage1': share(sum(gold_rank(c) >= 1 for c in right), len(right)),
        'disagreements': len(disagree),
        'router_right_when_disagreeing': share(sum(gold_rank(c) < 1 for c in disagree), len(disagree)),
        'net_if_router_followed': round((sum(gold_rank(c) < 1 for c in errors)
                                         - sum(gold_rank(c) >= 1 for c in right)) / len(evaluated), 4),
    }


def phase_b(bank: FactBank, profiles: Profiles, split: dict, cases: dict[str, CaseV2]) -> dict:
    evaluated = [cases[c] for c in set_ids(split, 'eval')]
    cascade, cascade_c1 = {}, {}
    for name, ids in split['eval'].items():
        preds = {p.case_id: p for p in load_predictions(CASCADE[name])}
        for cid in ids:
            p = preds.get(cid)
            cascade[cid] = p.predicted_code_10[:2] if p and p.status == 'completed' and p.predicted_code_10 else None
        if name == 'fresh':
            c1 = {p.case_id: p for p in load_predictions(CASCADE['fresh_c1'])}
            for cid in ids:
                p = c1.get(cid)
                cascade_c1[cid] = p.predicted_code_10[:2] if p and p.status == 'completed' and p.predicted_code_10 else None
    groups = sorted(profiles.groups)
    train_d1 = load_facts(bank, 'train_d1', PRIMARY)
    train_jev = load_facts(bank, 'train_jev', SECOND)
    eval_d1 = load_facts(bank, 'eval', PRIMARY)
    eval_jev = load_facts(bank, 'eval', SECOND)
    jev_ids = [c for c in split['sets']['train_jev'] if c in train_d1 and c in train_jev]
    nb = NaiveBayes(bank, groups, alpha=ALPHA).fit((train_d1[c]['facts'], group_of(cases[c])) for c in train_d1)
    nb_d1_small = NaiveBayes(bank, groups, alpha=ALPHA).fit((train_d1[c]['facts'], group_of(cases[c])) for c in jev_ids)
    nb_jev = NaiveBayes(bank, groups, alpha=ALPHA).fit((train_jev[c]['facts'], group_of(cases[c])) for c in jev_ids)
    bm25 = PrecedentIndex([cases[c] for c in split['sets']['train_d1']])

    def facts_of(rows, cid) -> Facts:
        r = rows.get(cid)
        return r['facts'] if r else {}

    routers: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for c in evaluated:
        f1, fj = facts_of(eval_d1, c.id), facts_of(eval_jev, c.id)
        prof, nbs = profile_scores(f1, profiles, eps=EPS), nb.scores(f1)
        routers['R3 закон+данные (d1.1, весь пул) — основной'][c.id] = rank(combine(nbs, prof))
        routers['R2 данные (d1.1, весь пул)'][c.id] = rank(nbs)
        routers['R1 закон: профили (d1.1)'][c.id] = rank(prof)
        routers['R3 (Jev, 1000)'][c.id] = rank(combine(nb_jev.scores(fj), profile_scores(fj, profiles, eps=EPS)))
        routers['R3 (d1.1, 1000)'][c.id] = rank(combine(nb_d1_small.scores(f1), prof))
        routers['R1 закон: профили (Jev)'][c.id] = rank(profile_scores(fj, profiles, eps=EPS))
        full = bm25_scores(bm25, c.input.description)
        routers['BM25 по обучающему пулу'][c.id] = rank(full)
        total = sum(full.values()) or 1.0
        bm25_log = {g: math.log((full.get(g, 0.0) / total) + 0.01) for g in groups}
        routers['R3 + BM25 (исследовательский)'][c.id] = rank(combine(nbs, prof, bm25_log))
    out = {'routers': {name: router_metrics(r, evaluated, cascade) for name, r in routers.items()},
           'cascade': {'top1': share(sum(cascade.get(c.id) == group_of(c) for c in evaluated), len(evaluated))}}
    fresh = [cases[c] for c in split['eval']['fresh']]
    out['cascade_c1_fresh'] = {'top1': share(sum(cascade_c1.get(c.id) == group_of(c) for c in fresh), len(fresh))}
    out['primary_on_c1_fresh'] = router_metrics(routers['R3 закон+данные (d1.1, весь пул) — основной'], fresh, cascade_c1)
    primary = out['routers']['R3 закон+данные (d1.1, весь пул) — основной']
    out['G1'] = {'rescue3': primary['rescue3']['share'], 'ok': (primary['rescue3']['share'] or 0) >= 0.50}
    out['G2'] = {'top3': primary['top3']['share'], 'ok': (primary['top3']['share'] or 0) >= 0.85}
    out['eval_coverage'] = {'d1.1': len(eval_d1), 'jev': len(eval_jev), 'n': len(evaluated)}
    return out


def report(a: dict, b: dict, bank: FactBank, profiles: Profiles) -> str:
    lines = [f'# Пилот движка фактов: фазы A и B ({bank.version}, {profiles.version})', '',
             '## Фаза A — банк вопросов', '',
             f'Серебряная разметка: {a["silver_n"]} описаний. «Нельзя определить» — доля ответов unknown на обучающих '
             'выборках; точность — совпадение с разметкой; точность «да/нет» — среди ответов не unknown.', '',
             '| Вопрос | unknown d1.1 | unknown Jev | согласие d1.1–Jev | точность d1.1 | точность Jev | '
             'точность d1.1 там, где вопрос относится к товару | информация о группе, бит |',
             '|---|---|---|---|---|---|---|---|']
    for qid, r in a['questions'].items():
        s1, sj = r[f'silver_{PRIMARY}'], r[f'silver_{SECOND}']
        lines.append(f"| {qid}{' (условный)' if r['conditional'] else ''} | {pct(r[f'unknown_{PRIMARY}']['share'])} "
                     f"| {pct(r[f'unknown_{SECOND}']['share'])} | {pct(r['agreement']['share'])} "
                     f"| {pct(s1['accuracy']['share'])} | {pct(sj['accuracy']['share'])} "
                     f"| {pct((s1['applicable_accuracy'] or {}).get('share'))} | {r['mutual_information_bits']} |")
    g3 = a['G3']
    lines += ['', f"**G3:** {len(g3['passed'])} из {g3['core_questions']} базовых вопросов проходят "
              f"(unknown ≤ 15% и точность ≥ 80% у d1.1): {', '.join(g3['passed'])} — {'выполнено' if g3['ok'] else 'не выполнено'}.",
              '', '### Исполнители на серебряной разметке (базовые вопросы)', '',
              '| Модель | кейсов | точность | unknown | $ на кейс |', '|---|---|---|---|---|']
    for name, r in a['executors_on_silver'].items():
        lines.append(f"| {name} | {r['n_cases']} | {pct(r['core_accuracy']['share'])} | {pct(r['core_unknown']['share'])} "
                     f"| {r['usd_per_case']} |")
    lines += ['', '## Фаза B — маршрутизация к группам', '',
              f"Оценка: dev148 + fresh145, {b['routers'][next(iter(b['routers']))]['n']} кейсов; каскад (R0 на dev, "
              f"C0 на fresh) верно выбрал группу в {pct(b['cascade']['top1']['share'])}.", '',
              '| Маршрутизатор | top-1 | top-3 | ошибки каскада: эталон в top-3 | эталон выше группы каскада | '
              'на верных у каскада top-1 другой | при несогласии прав маршрутизатор | итог, если всегда слушать маршрутизатор |',
              '|---|---|---|---|---|---|---|---|']
    for name, r in b['routers'].items():
        lines.append(f"| {name} | {pct(r['top1']['share'])} | {pct(r['top3']['share'])} | {pct(r['rescue3']['share'])} "
                     f"({r['cascade_errors']}) | {pct(r['gold_above_cascade']['share'])} | {pct(r['damage1']['share'])} "
                     f"| {pct(r['router_right_when_disagreeing']['share'])} | {r['net_if_router_followed']:+.1%} |")
    p = b['routers']['R3 закон+данные (d1.1, весь пул) — основной']
    lines += ['', f"**G1:** эталон в top-3 основного маршрутизатора на ошибках каскада — {pct(b['G1']['rescue3'])} "
              f"(95% ДИ {p['rescue3_ci95']}), порог 50% — {'выполнено' if b['G1']['ok'] else 'не выполнено'}.",
              f"**G2:** top-3 — {pct(b['G2']['top3'])} (95% ДИ {p['top3_ci95']}), порог 85% — "
              f"{'выполнено' if b['G2']['ok'] else 'не выполнено'}.", '',
              f"Каскад с прецедентами (C1) на fresh145 верно выбрал группу в {pct(b['cascade_c1_fresh']['top1']['share'])}; "
              f"основной маршрутизатор относительно C1: эталон в top-3 на его ошибках — "
              f"{pct(b['primary_on_c1_fresh']['rescue3']['share'])} ({b['primary_on_c1_fresh']['cascade_errors']}).", '']
    verdict = ('фаза C оправдана' if a['G3']['ok'] and b['G1']['ok'] and b['G2']['ok'] else
               'факты — для вопросов и мер, не для навигации' if a['G3']['ok'] else 'сначала банк v2')
    lines += [f'**Решение по §5 предрегистрации:** {verdict}.', '']
    return '\n'.join(lines)


def score() -> None:
    bank = load_bank()
    profiles = load_profiles(bank)
    split = load_split()
    cases = cases_by_id()
    a = phase_a(bank, split, cases)
    b = phase_b(bank, profiles, split, cases)
    out_dir = RESULTS / bank.version
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {'bank': {'version': bank.version, 'sha256': bank.sha256},
               'profiles': {'version': profiles.version, 'sha256': profiles.sha256}, 'phase_a': a, 'phase_b': b}
    (out_dir / 'facts_pilot_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1) + '\n',
                                                      encoding='utf-8')
    text = report(a, b, bank, profiles)
    (out_dir / 'facts_pilot_report.md').write_text(text, encoding='utf-8')
    print(text)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('split')
    c = sub.add_parser('collect')
    c.add_argument('--set', required=True, choices=['train_d1', 'train_jev', 'silver', 'smoke', 'eval'])
    c.add_argument('--model', required=True, choices=sorted(PRICE))
    c.add_argument('--max-usd', type=float, required=True)
    c.add_argument('--max-cases', type=int)
    c.add_argument('--concurrency', type=int, default=6)
    sub.add_parser('score')
    args = p.parse_args()
    if args.command == 'split':
        build_split()
    elif args.command == 'collect':
        asyncio.run(collect(args))
    else:
        score()


if __name__ == '__main__':
    main()
