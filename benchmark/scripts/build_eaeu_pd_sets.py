"""Build tier-A sets from the EAEU collection of preliminary decisions (ЕС 999).

Real goods, codes set by customs authorities of the member states. From a raw
snapshot (collect_eaeu_predecisions) the builder:

1. keeps records whose code is a current leaf of the nomenclature and whose
   description is 80–5989 characters (6000 is the registry's cut);
2. drops exact duplicate descriptions and descriptions given different codes;
3. samples a locked test set and a dev set, stratified by section with weights
   √(share of the section in KG imports, UN Comtrade 2024), at least 8 per
   section where the pool allows, at most one case per ten-digit code and two
   per six-digit subheading; dev shares no ten-digit code with test;
4. puts every decision of the Kyrgyz customs into test.

Deterministic for a given snapshot, nomenclature and seed. The descriptions are
published by the authorities in anonymised form; the decision date is not, so
as_of_date stays empty and money is priced on the scorer's --money-date.

Run:
    python -m benchmark.scripts.build_eaeu_pd_sets --snapshot 2026-10-18
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

import psycopg2

from benchmark.collectors.eaeu_predecisions import TITLE, load_records
from benchmark.evaluator.fingerprint import libpq_dsn
from benchmark.schema.case_v2 import (
    CaseV2,
    ClassificationGold,
    GoldLayers,
    GoldTier,
    PassportInput,
    Provenance,
    SourceRef,
    Sufficiency,
    Track,
    Verification,
    save_cases,
    section_of,
    write_manifest,
)

RAW_ROOT = Path('benchmark/data/raw/eaeu_predecisions')
COMTRADE = Path('benchmark/data/raw/comtrade/kg_imports_hs2_2024.json')
OUT_DIR = Path('benchmark/data/cases/v2')

MIN_CHARS, MAX_CHARS = 80, 5989
SECTION_FLOOR = 8
PER_SUBHEADING = 2

COUNTRIES = {'КАЗАХСТАН': 'KZ', 'РОССИЯ': 'RU', 'БЕЛАРУСЬ': 'BY', 'КЫРГЫЗСТАН': 'KG',
             'АРМЕНИЯ': 'AM'}

_RULE_ORDER = ['1', '2а', '2б', '3а', '3б', '3в', '4', '5а', '5б', '6']


def normalize(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def parse_rules(justification: str) -> list[str]:
    """'В соответствии с ОПИ 1 (примечание 4 к разделу XVI), 3 б) и 6' → ['ОПИ 1', 'ОПИ 3б', 'ОПИ 6']."""
    text = normalize(justification).lower()
    text = re.sub(r'(\d)\s*\(?\s*([абв])\s*\)', r'\1\2', text)  # «2 а)», «5(б)» → «2а», «5б»
    for _ in range(3):  # references to notes and explanations carry their own numbers
        text = re.sub(r'\([^()]*[а-яё]{4,}[^()]*\)', ' ', text)
    # …and so do notes named inline: «примечанием 2а к разделу xvi»
    text = re.sub(r'примечани\w*\s+\d+[абв]?\s+к\s+(?:раздел\w*|групп\w*)\s+[\dxvil]+', ' ', text)
    start = max(text.find('опи'), text.find('интерпретации'))
    if start < 0:
        return []
    segment = re.split(r',\s*(?:общие|пояснени|положени)|тн\s*вэд|;', text[start:])[0]
    found = set(re.findall(r'(?<![\d.])([1-6][абв]?)(?![\d.])', segment))
    return [f'ОПИ {rule}' for rule in _RULE_ORDER if rule in found]


def import_shares(path: Path) -> dict[str, float]:
    """Share of each HS chapter in KG imports (chapter 99, unspecified, left out)."""
    rows = json.loads(path.read_text(encoding='utf-8'))['data']
    values = {r['cmdCode']: float(r['primaryValue'] or 0) for r in rows if r['cmdCode'] != '99'}
    total = sum(values.values())
    return {chapter: value / total for chapter, value in values.items()}


def pool(records: list[dict], leaves: set[str]) -> tuple[list[dict], Counter]:
    """Usable records, one per description; the reasons for dropping the rest."""
    dropped: Counter = Counter()
    codes_by_description: dict[str, set[str]] = defaultdict(set)
    rows = []
    for record in records:
        data = record['data']
        code = re.sub(r'\D', '', data.get('CodeTNVED') or '')
        description = normalize(data.get('Description') or '')
        if code not in leaves:
            dropped['code not a current leaf'] += 1
            continue
        if not MIN_CHARS <= len(description) <= MAX_CHARS:
            dropped['description length'] += 1
            continue
        key = description.lower()
        codes_by_description[key].add(code)
        rows.append({'record': record, 'code': code, 'description': description, 'key': key})
    conflicts = {k for k, codes in codes_by_description.items() if len(codes) > 1}
    seen: set[str] = set()
    unique = []
    for row in sorted(rows, key=lambda r: r['record']['id']):
        if row['key'] in conflicts:
            dropped['same description, different codes'] += 1
        elif row['key'] in seen:
            dropped['duplicate description'] += 1
        else:
            seen.add(row['key'])
            unique.append(row)
    return unique, dropped


def quotas(n: int, available: dict[str, int], weights: dict[str, float]) -> dict[str, int]:
    """n split over sections ∝ weight, a floor where the pool allows, capped by availability."""
    sections = [s for s in available if available[s]]
    floor = min(SECTION_FLOOR, n // len(sections)) if sections else 0
    quota = {s: min(floor, available[s]) for s in sections}
    while sum(quota.values()) < n:
        open_ = [s for s in sections if quota[s] < available[s]]
        if not open_:
            break
        total = sum(weights.get(s, 0) for s in open_) or len(open_)
        # Largest deficit against the weighted target gets the next case.
        target = {s: n * (weights.get(s, 0) or 1e-9) / total for s in open_}
        best = max(open_, key=lambda s: (target[s] - quota[s], s))
        quota[best] += 1
    return quota


def sample(rows: list[dict], n: int, rng: random.Random, weights: dict[str, float],
           group_weights: dict[str, float], *, forced: list[dict],
           exclude_codes: set[str]) -> list[dict]:
    by_section: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row['code'] not in exclude_codes:
            by_section[section_of(row['code'])].append(row)
    taken_codes = {row['code'] for row in forced}
    per_subheading = Counter(row['code'][:6] for row in forced)
    picked = list(forced)
    available = {s: len({r['code'] for r in rs}) for s, rs in by_section.items()}
    plan = quotas(n, available, weights)
    for row in forced:
        plan[section_of(row['code'])] = max(0, plan.get(section_of(row['code']), 0) - 1)
    for section in sorted(plan):
        by_group: dict[str, list[dict]] = defaultdict(list)
        for row in by_section[section]:
            by_group[row['code'][:2]].append(row)
        for group_rows in by_group.values():
            rng.shuffle(group_rows)
        need = plan[section]
        while need > 0 and by_group:
            groups = sorted(by_group)
            group = rng.choices(groups, weights=[group_weights.get(g, 0) + 1e-3 for g in groups])[0]
            row = by_group[group].pop()
            if not by_group[group]:
                del by_group[group]
            if row['code'] in taken_codes or per_subheading[row['code'][:6]] >= PER_SUBHEADING:
                continue
            picked.append(row)
            taken_codes.add(row['code'])
            per_subheading[row['code'][:6]] += 1
            need -= 1
    return picked


def to_case(row: dict, *, split: str, page_sha: dict[str, str], snapshot: dict) -> CaseV2:
    record = row['record']
    data = record['data']
    country = COUNTRIES.get(data['Country']['name'], data['Country']['name'])
    justification = normalize(data.get('Justification') or '')
    return CaseV2(
        id=f"PD-{country}-{record['id'][:8]}",
        track=Track.PASSPORT,
        split=split,
        tier=GoldTier.NORMATIVE,
        provenance=Provenance(
            kind='preliminary_decision',
            reference=f"{TITLE}: запись {record['id']}, таможенный орган — {data['Country']['name']}",
            sources=[SourceRef(
                title=f"НСИ ЕАЭС, справочник 1999, версия записи {record['versionId']}",
                url='https://nsi.eaeunion.org/portal/1999',
                retrieved_on=snapshot['on_date'],
                sha256=page_sha[record['id']],
            )],
        ),
        tags=sorted({f'authority:{country}', *(['kg_customs'] if country == 'KG' else [])}),
        input=PassportInput(description=row['description']),
        gold=GoldLayers(classification=ClassificationGold(
            code=row['code'],
            verification=Verification.OFFICIAL,
            # The authority saw samples and documents; whether the published text alone
            # fixes the code is not established.
            sufficiency=Sufficiency.UNKNOWN,
            opi_expected=parse_rules(justification),
        )),
        notes=f'Обоснование органа: {justification}' if justification else None,
    )


def _leaves() -> set[str]:
    with closing(psycopg2.connect(libpq_dsn())) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT code FROM tnved_tree WHERE is_leaf')
        return {code for (code,) in cursor.fetchall()}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--snapshot', required=True, help='Folder under benchmark/data/raw/eaeu_predecisions')
    p.add_argument('--test', type=int, default=400)
    p.add_argument('--dev', type=int, default=150)
    p.add_argument('--seed', type=int, default=20261018)
    args = p.parse_args()

    raw_dir = RAW_ROOT / args.snapshot
    records, snapshot = load_records(raw_dir)
    page_sha = {}
    for page in snapshot['pages']:
        for record in json.loads((raw_dir / page['file']).read_bytes()):
            page_sha[record['id']] = page['sha256']

    rows, dropped = pool(records, _leaves())
    chapter_share = import_shares(COMTRADE)
    section_share: dict[str, float] = defaultdict(float)
    for chapter, share in chapter_share.items():
        section_share[section_of(chapter + '00000000')] += share
    weights = {s: math.sqrt(v) for s, v in section_share.items()}
    group_weights = {g: math.sqrt(v) for g, v in chapter_share.items()}

    rng = random.Random(args.seed)
    kg = [r for r in rows if r['record']['data']['Country']['name'] == 'КЫРГЫЗСТАН']
    test = sample(rows, args.test, rng, weights, group_weights, forced=kg, exclude_codes=set())
    test_ids = {r['record']['id'] for r in test}
    dev = sample([r for r in rows if r['record']['id'] not in test_ids], args.dev, rng, weights,
                 group_weights, forced=[], exclude_codes={r['code'] for r in test})

    built_from = {
        'snapshot': {'path': raw_dir.as_posix(), 'on_date': snapshot['on_date'],
                     'fetched_at': snapshot['fetched_at'], 'records': snapshot['records'],
                     'manifest_sha256': hashlib.sha256(
                         (raw_dir / 'manifest.json').read_bytes()).hexdigest()},
        'pool': {'usable': len(rows), 'dropped': dict(dropped)},
        'stratification': {
            'weights': 'sqrt(share of section in KG imports 2024, UN Comtrade AG2)',
            'comtrade_sha256': hashlib.sha256(COMTRADE.read_bytes()).hexdigest(),
            'section_share': {s: round(v, 4) for s, v in sorted(section_share.items())},
            'floor_per_section': SECTION_FLOOR, 'max_per_subheading': PER_SUBHEADING,
            'one_case_per_code': True, 'seed': args.seed,
        },
    }
    for name, chosen, split, purpose in (
        (f'eaeu_pd_test{args.test}', test, 'test',
         'LOCKED test: real goods, codes of EAEU customs authorities. Run before releases; '
         'never tune prompts on its per-case errors.'),
        (f'eaeu_pd_dev{args.dev}', dev, 'dev',
         'Dev set for error analysis and tuning; no ten-digit code shared with the test set.'),
    ):
        cases = sorted((to_case(r, split=split, page_sha=page_sha, snapshot=snapshot)
                        for r in chosen), key=lambda c: c.id)
        path = OUT_DIR / f'{name}.jsonl'
        save_cases(cases, path)
        write_manifest(path, cases, purpose=purpose, built_from=built_from)
        sections = Counter(c.section for c in cases)
        print(f'{len(cases)} → {path}; sections {dict(sorted(sections.items()))}')
    print(f'pool {len(rows)} usable; dropped {dict(dropped)}')


if __name__ == '__main__':
    main()
