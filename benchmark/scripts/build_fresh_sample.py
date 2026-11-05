"""A fresh evaluation sample from the calibration pool, and the precedent exclusions for it.

For a decisive re-test after work on dev150 (pre-registration of stage 1, §8; navigation
E-N3): the sample never saw tuning. It is drawn from ``pools/eaeu_pd_calibration`` with
the stratification of the dev and test sets (``build_eaeu_pd_sets.sample``: sections by
√ of their share in KG imports, one case per ten-digit code, at most two per
subheading), without the records, codes and near-duplicates of the sets in ``--avoid``.

``--exclusions`` also writes the ids of ``classification_precedents`` rows the cascade
must not see during the run (``PRECEDENTS_EXCLUDE_PATH``): everything outside the
replay pool (holdout and its near-duplicates), the sampled records and their
near-duplicates (``benchmark.precedents.exclusion_ids``).

Run:
    python -m benchmark.scripts.build_fresh_sample --name eaeu_pd_fresh150 --n 150 --seed 20261106 \\
        --avoid benchmark/data/cases/v2/eaeu_pd_dev150.jsonl \\
        --exclusions benchmark/data/cases/v2/eaeu_pd_fresh150.precedents_exclude.txt
"""
from __future__ import annotations

import argparse
import hashlib
import math
import random
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import psycopg2

from benchmark.evaluator.fingerprint import libpq_dsn
from benchmark.holdout import NEAR_DUPLICATE, Holdout, file_sha256, shingles, source_of
from benchmark.precedents import exclusion_ids
from benchmark.schema.case_v2 import load_cases, save_cases, section_of, write_manifest
from benchmark.scripts.build_eaeu_pd_sets import COMTRADE, import_shares, sample

POOLS = Path('benchmark/data/cases/v2/pools')
OUT_DIR = Path('benchmark/data/cases/v2')


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--name', required=True)
    p.add_argument('--n', type=int, default=150)
    p.add_argument('--seed', type=int, default=20261106)
    p.add_argument('--avoid', type=Path, action='append', default=[], help='Sets whose records, codes and '
                   'near-duplicates the sample must not contain')
    p.add_argument('--exclusions', type=Path, help='Write precedent exclusions for an end-to-end run here')
    args = p.parse_args()

    holdout = Holdout.load()
    pool_path = POOLS / 'eaeu_pd_calibration.jsonl'
    pool = holdout.check(load_cases(pool_path), 'calibration')
    avoid = [c for path in args.avoid for c in load_cases(path)]
    avoid_sources = {source_of(c) for c in avoid}
    avoid_codes = {c.gold.classification.code for c in avoid}
    avoid_grams = [shingles(c.input.description) for c in avoid]

    def near(text: str) -> bool:
        g = shingles(text)
        return any(g & o and len(g & o) / len(g | o) >= NEAR_DUPLICATE for o in avoid_grams)

    candidates = [c for c in pool if source_of(c) not in avoid_sources and not near(c.input.description)]
    rows = [{'record': {'id': source_of(c)}, 'code': c.gold.classification.code,
             'description': c.input.description, 'key': c.input.description.lower(), 'case': c}
            for c in candidates]
    chapter_share = import_shares(COMTRADE)
    section_share: dict[str, float] = defaultdict(float)
    for chapter, share in chapter_share.items():
        section_share[section_of(chapter + '00000000')] += share
    weights = {s: math.sqrt(v) for s, v in section_share.items()}
    group_weights = {g: math.sqrt(v) for g, v in chapter_share.items()}
    chosen = sample(rows, args.n, random.Random(args.seed), weights, group_weights, forced=[],
                    exclude_codes=avoid_codes)
    cases = sorted((r['case'] for r in chosen), key=lambda c: c.id)
    for case in cases:
        case.tags = sorted({*case.tags, args.name})
    path = OUT_DIR / f'{args.name}.jsonl'
    save_cases(cases, path)
    write_manifest(path, cases, purpose='Fresh sample from the calibration pool for a decisive re-test; never '
                   'used for tuning.', built_from={
                       'pool': {'path': pool_path.as_posix(), 'sha256': file_sha256(pool_path),
                                'usable': len(candidates)},
                       'avoid': [{'path': a.as_posix(), 'sha256': file_sha256(a)} for a in args.avoid],
                       'seed': args.seed, 'stratification': 'build_eaeu_pd_sets.sample, sqrt(KG import share)',
                       'comtrade_sha256': hashlib.sha256(COMTRADE.read_bytes()).hexdigest()})
    print(f'{len(cases)} → {path} (from {len(candidates)} usable pool records)')

    if args.exclusions:
        with closing(psycopg2.connect(libpq_dsn())) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT id FROM classification_precedents')
            all_ids = [rid for (rid,) in cursor.fetchall()]
        replay = holdout.check(load_cases(POOLS / 'eaeu_pd_replay.jsonl'), 'replay')
        hidden = exclusion_ids(all_ids, replay, cases)
        args.exclusions.write_text('\n'.join(hidden) + '\n', encoding='utf-8', newline='\n')
        print(f'{len(hidden)} of {len(all_ids)} precedents hidden → {args.exclusions}')


if __name__ == '__main__':
    main()
