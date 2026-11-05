"""Pools for tuning from the EAEU collection of preliminary decisions, without holdout.

Every usable record of the snapshot (the same filter as build_eaeu_pd_sets:
current leaf, 80–5989 characters, one record per description) becomes a case;
``benchmark.holdout`` drops what must not be tuned on:

- ``eaeu_pd_calibration`` — thresholds, signal policy, question wording: no holdout
  records, no records with their ten-digit codes, no near-duplicate texts;
- ``eaeu_pd_replay`` — replay of family specifications: no holdout records and no
  near-duplicates; records sharing a code with holdout stay.

The pools are written next to the sets, in ``pools/``; the JSONL files are
large and stay out of git, the manifests are committed. A consumer reads a pool
with ``benchmark.holdout.load_pool``, which fails on any overlap.

Run:
    python -m benchmark.scripts.build_pools --snapshot 2026-10-18
"""
from __future__ import annotations

import argparse
import hashlib
import json

from benchmark.collectors.eaeu_predecisions import load_records
from benchmark.holdout import MANIFEST, Holdout, file_sha256, load_pool
from benchmark.schema.case_v2 import save_cases, write_manifest
from benchmark.scripts.build_eaeu_pd_sets import OUT_DIR, RAW_ROOT, _leaves, pool, to_case

POOL_DIR = OUT_DIR / 'pools'

PURPOSES = {
    'calibration': 'Thresholds, signal policy and question wording are chosen here, never on '
                   'holdout. No holdout record, ten-digit code or near-duplicate text.',
    'replay': 'Replay of family specifications. No holdout record or near-duplicate text; '
              'records sharing a code with holdout stay.',
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--snapshot', required=True, help='Folder under benchmark/data/raw/eaeu_predecisions')
    args = p.parse_args()

    raw_dir = RAW_ROOT / args.snapshot
    records, snapshot = load_records(raw_dir)
    page_sha = {}
    for page in snapshot['pages']:
        for record in json.loads((raw_dir / page['file']).read_bytes()):
            page_sha[record['id']] = page['sha256']
    rows, dropped = pool(records, _leaves())
    cases = [to_case(row, split='dev', page_sha=page_sha, snapshot=snapshot) for row in rows]

    holdout = Holdout.load()
    for purpose, text in PURPOSES.items():
        chosen = holdout.pool(cases, purpose)
        for case in chosen.cases:
            case.tags = sorted({*case.tags, f'pool:{purpose}'})
        path = POOL_DIR / f'eaeu_pd_{purpose}.jsonl'
        save_cases(sorted(chosen.cases, key=lambda c: c.id), path)
        write_manifest(path, chosen.cases, purpose=text, built_from={
            'snapshot': {'path': raw_dir.as_posix(), 'on_date': snapshot['on_date'],
                         'records': snapshot['records'],
                         'manifest_sha256': hashlib.sha256(
                             (raw_dir / 'manifest.json').read_bytes()).hexdigest()},
            'usable': len(rows),
            'dropped_before_holdout': dict(dropped),
            'holdout': {'manifest_sha256': file_sha256(MANIFEST),
                        'excluded': chosen.summary()},
        })
        load_pool(path, purpose, holdout)  # the file as written must pass the guard
        print(f'{purpose}: {len(chosen.cases)} of {len(rows)} → {path}; excluded {chosen.summary()}')


if __name__ == '__main__':
    main()
