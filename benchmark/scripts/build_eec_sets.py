"""Build tier-A sets from EEC Collegium classification decisions.

A decision defines the product for classification and fixes the code, binding
in every member state — often only to the heading. Items are kept when the
decision is in force (not amended or cancelled in the EEC list) and the code
still opens at least one current leaf.

- decided since 2022 (nomenclature edition 2022): locked test, verification official;
- decided 2017–2021: dev, unverified — HS 2022 may have moved such goods even
  where the old code still exists;
- older decisions are left out.

Run:
    python -m benchmark.scripts.build_eec_sets
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from collections import Counter
from contextlib import closing
from pathlib import Path

import psycopg2

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
    write_manifest,
)
from benchmark.scripts.build_eaeu_pd_sets import parse_rules

RAW_DIR = Path('benchmark/data/raw/eec_decisions')
OUT_DIR = Path('benchmark/data/cases/v2')
EDITION_2022 = '2022-01-01'
OLDEST = '2017-01-01'


def opens_leaf(code: str, leaves: list[str]) -> bool:
    return any(leaf.startswith(code) for leaf in leaves)


def items(decisions: list[dict], leaves: list[str]) -> tuple[list[tuple[dict, dict]], Counter]:
    kept, dropped = [], Counter()
    seen: set[tuple[str, str]] = set()
    for decision in decisions:
        if decision['status'] != 'in_force':
            dropped[f"decision {decision['status']}"] += 1
            continue
        if not decision['date'] or decision['date'] < OLDEST:
            dropped['decided before 2017'] += 1
            continue
        if not decision['items']:
            dropped[f"text not parsed: {decision.get('error') or 'no items'}"] += 1
            continue
        for item in decision['items']:
            key = (item['description'].lower(), item['code'])
            if len(item['code']) not in (4, 6, 8, 9, 10):
                dropped['code length'] += 1
            elif not opens_leaf(item['code'], leaves):
                dropped['code not in current nomenclature'] += 1
            elif key in seen:
                dropped['duplicate item'] += 1
            else:
                seen.add(key)
                kept.append((decision, item))
    return kept, dropped


def sources(decision: dict, retrieved_on: dt.date) -> list[SourceRef]:
    refs = [SourceRef(title=f"Решение Коллегии ЕЭК № {decision['number']}", url=decision['url'],
                      retrieved_on=retrieved_on, sha256=decision['sha256'])]
    if decision.get('text_source') == 'zakon.uchet.kz':
        refs.append(SourceRef(
            title='Текст решения: zakon.uchet.kz (русская версия у ЕЭК — скан без текста)',
            url=decision['text_url'], retrieved_on=retrieved_on, sha256=decision['text_sha256'],
        ))
    return refs


def to_case(decision: dict, item: dict, *, retrieved_on: dt.date) -> CaseV2:
    current = decision['date'] >= EDITION_2022
    year = decision['date'][:4]
    return CaseV2(
        id=f"EEC-{year}-{decision['number']}-{item['item']}",
        track=Track.PASSPORT,
        split='test' if current else 'dev',
        tier=GoldTier.NORMATIVE,
        provenance=Provenance(
            kind='eec_decision',
            reference=(f"Решение Коллегии ЕЭК от {decision['date']} № {decision['number']}, "
                       f"п. {item['item']}: {decision['title']}"),
            sources=sources(decision, retrieved_on),
        ),
        tags=sorted({'eec_decision', f'decision_year:{year}',
                     *(['multi_item'] if len(decision['items']) > 1 else []),
                     *([] if current else ['pre_hs2022'])}),
        input=PassportInput(description=item['description']),
        gold=GoldLayers(classification=ClassificationGold(
            code=item['code'],
            verification=Verification.OFFICIAL if current else Verification.UNVERIFIED,
            # The decision text is the legal definition of the product for this code.
            sufficiency=Sufficiency.SUFFICIENT,
            opi_expected=parse_rules(f"ОПИ {item['rules']}"),
        )),
        notes=(f"Уровень в решении: {item['level']}; коды в перечне ЕЭК: "
               f"{', '.join(decision['codes']) or '—'}"
               + ('' if current else '. Решение по редакции ТН ВЭД до 2022 года: '
                  'применимость к редакции 2022 не проверена.')),
    )


def _leaves() -> list[str]:
    with closing(psycopg2.connect(libpq_dsn())) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT code FROM tnved_tree WHERE is_leaf')
        return sorted(code for (code,) in cursor.fetchall())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--raw-dir', type=Path, default=RAW_DIR)
    args = p.parse_args()
    raw = args.raw_dir / 'decisions.json'
    decisions = json.loads(raw.read_text(encoding='utf-8'))
    retrieved_on = dt.date.fromtimestamp(raw.stat().st_mtime)
    kept, dropped = items(decisions, _leaves())
    cases = [to_case(d, i, retrieved_on=retrieved_on) for d, i in kept]
    built_from = {
        'decisions': {'path': raw.as_posix(), 'sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
                      'n': len(decisions)},
        'kept_items': len(kept), 'dropped': dict(dropped),
        'rules': {'in_force_only': True, 'test_from': EDITION_2022, 'dev_from': OLDEST},
    }
    for split, name, purpose in (
        ('test', 'eec_decisions_test',
         'LOCKED test: EEC Collegium decisions since the 2022 edition, binding in all member '
         'states; gold may stop at the heading. Never tune on its per-case errors.'),
        ('dev', 'eec_decisions_dev',
         'Dev: EEC decisions of 2017–2021, not re-verified against the 2022 edition.'),
    ):
        chosen = sorted((c for c in cases if c.split == split), key=lambda c: c.id)
        path = OUT_DIR / f'{name}.jsonl'
        save_cases(chosen, path)
        write_manifest(path, chosen, purpose=purpose, built_from=built_from)
        digits = Counter(c.gold.classification.digits for c in chosen)
        print(f'{len(chosen)} → {path}; gold digits {dict(sorted(digits.items()))}')
    print(f'dropped {dict(dropped)}')


if __name__ == '__main__':
    main()
