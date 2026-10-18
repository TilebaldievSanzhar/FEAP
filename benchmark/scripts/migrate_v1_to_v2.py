"""Convert benchmark_100 (v1) and the v32 label audit into the v2 case schema.

The 100 descriptions and labels were written by the team (tier D). The v32
audit reviewed 15 of them against the official EEC tariff PDFs: corrections
become source-reviewed gold, quarantines stay out of headline numbers. The
later recheck (docs/sgr_v32_label_recheck.md) withdrew the unconditional H30
correction, so H30 is disputed; both decisions stay in its review history.
The other 85 labels were never re-certified and are marked unverified.

Run:
    python -m benchmark.scripts.migrate_v1_to_v2
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from benchmark.schema.case_v2 import (
    CaseV2,
    ClassificationGold,
    ConditionalCode,
    GoldLayers,
    GoldReview,
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
from benchmark.schema.test_case import TestCase

V1_PATH = Path('benchmark/data/cases/benchmark_100.jsonl')
AUDIT_PATH = Path('benchmark/data/results/v32_label_audit.json')
RECHECK_PATH = Path('docs/sgr_v32_label_recheck.md')
OUT_PATH = Path('benchmark/data/cases/v2/passport_regression100.jsonl')

AUDIT_DATE = dt.date(2026, 9, 21)
AUDIT_BY = 'team source review (v32 label audit), not an expert ruling'

#: docs/sgr_v32_label_recheck.md, «H30»: the replacement holds only for a speaker
#: without a microphone and hands-free calling; the description does not say.
H30_RECHECK = GoldReview(
    on=AUDIT_DATE,
    by='team source review (v32 label recheck), not an expert ruling',
    disposition='disputed',
    previous_code_10='8518220009',
    reason=(
        'Два динамика в одном корпусе обосновывают 851822 для обычной колонки. Решение Коллегии '
        'ЕЭК № 150 от 24.11.2020 относит портативную систему с Bluetooth, аккумулятором, '
        'микрофоном и громкой связью к 8518309500. Наличие микрофона и громкой связи в описании '
        'не установлено: безусловный эталон отозван.'
    ),
    sources=[SourceRef(
        title='Решение Коллегии ЕЭК № 150 от 24.11.2020', url='https://adilet.zan.kz/rus/docs/H20EK000150',
    )],
)
H30_CONDITIONAL = [
    ConditionalCode(code_10='8518220009', condition='колонка без микрофона и громкой связи'),
    ConditionalCode(code_10='8518309500', condition='есть микрофон и громкая связь (Решение № 150)'),
]

_GROUP_IN_URL = re.compile(r'ru\.(\d{2})_')


def _source(raw: dict) -> SourceRef:
    group = _GROUP_IN_URL.search(raw['url'])
    title = f'ЕТТ ЕАЭС, группа {group.group(1)}, PDF ЕЭК' if group else 'ЕТТ ЕАЭС, PDF ЕЭК'
    return SourceRef(
        title=title, url=raw['url'], pages=raw.get('pdf_pages_one_based', []),
        retrieved_on=dt.date.fromisoformat(raw['retrieved_on']) if raw.get('retrieved_on') else None,
        sha256=raw.get('sha256'),
    )


def gold_from_audit(case: TestCase, review: dict | None) -> ClassificationGold:
    """Gold of one v1 case after the v32 audit (review is None if not audited)."""
    truth = case.ground_truth
    common = dict(
        trap_codes=case.trap_codes, opi_expected=case.opi_rules_expected,
        requires_backtrack=case.requires_backtrack,
    )
    if review is None:
        return ClassificationGold(
            code_10=truth.code_10, verification=Verification.UNVERIFIED,
            sufficiency=Sufficiency.UNKNOWN, **common,
        )
    source = _source(review['source'])
    if review['disposition'] == 'corrected':
        history = [GoldReview(
            on=AUDIT_DATE, by=AUDIT_BY, disposition='corrected',
            previous_code_10=review['old_code_10'], reason=review['reason'], sources=[source],
        )]
        if case.id == 'BENCH-H30':
            return ClassificationGold(
                code_10=review['replacement_code_10'], conditional=H30_CONDITIONAL,
                verification=Verification.DISPUTED, sufficiency=Sufficiency.INSUFFICIENT,
                missing_facts=['наличие микрофона и функции громкой связи'],
                reviews=[*history, H30_RECHECK], **common,
            )
        return ClassificationGold(
            code_10=review['replacement_code_10'], verification=Verification.SOURCE_REVIEWED,
            sufficiency=Sufficiency.SUFFICIENT, reviews=history, **common,
        )
    if review['disposition'] == 'quarantined_exact10':
        return ClassificationGold(
            code_10=truth.code_10, verification=Verification.QUARANTINED,
            sufficiency=Sufficiency.INSUFFICIENT, missing_facts=[review['reason']],
            reviews=[GoldReview(
                on=AUDIT_DATE, by=AUDIT_BY, disposition='quarantined',
                reason=review['reason'], sources=[source],
            )],
            **common,
        )
    raise ValueError(f"{case.id}: unknown audit disposition {review['disposition']!r}")


def migrate_case(case: TestCase, review: dict | None) -> CaseV2:
    tags = []
    if case.requires_backtrack:
        tags.append('backtrack')
    if case.requires_clarification:
        # v1 passes the missing parameter in additional_info up front.
        tags.append('answer_in_additional_info')
    if review is not None:
        tags.append('v32_audited')
    return CaseV2(
        id=case.id,
        track=Track.PASSPORT,
        split='regression',
        tier=GoldTier.SYNTHETIC,
        provenance=Provenance(kind='synthetic', reference=case.source),
        difficulty=case.difficulty.value,
        tags=tags,
        input=PassportInput(
            description=case.product_description, additional_info=case.additional_info,
        ),
        gold=GoldLayers(classification=gold_from_audit(case, review)),
        notes=case.notes,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--v1', type=Path, default=V1_PATH)
    p.add_argument('--audit', type=Path, default=AUDIT_PATH)
    p.add_argument('--out', type=Path, default=OUT_PATH)
    args = p.parse_args()

    audit = json.loads(args.audit.read_text(encoding='utf-8'))
    if audit['original_sha256'] != _sha256(args.v1):
        raise SystemExit(f'{args.audit} audited a different version of {args.v1}')
    reviews = {r['case_id']: r for r in audit['reviews']}

    with args.v1.open(encoding='utf-8') as f:
        v1_cases = [TestCase.model_validate_json(line) for line in f if line.strip()]
    unknown = set(reviews) - {c.id for c in v1_cases}
    if unknown:
        raise SystemExit(f'Audit reviews cases missing from {args.v1}: {sorted(unknown)}')

    cases = [migrate_case(c, reviews.get(c.id)) for c in v1_cases]
    save_cases(cases, args.out)
    manifest = write_manifest(
        args.out, cases,
        purpose=('Synthetic regression set (tier D), already used for tuning: comparisons '
                 'with past runs only, not an estimate of accuracy on real goods.'),
        built_from={
            'v1': {'path': args.v1.as_posix(), 'sha256': _sha256(args.v1)},
            'audit': {'path': args.audit.as_posix(), 'sha256': _sha256(args.audit)},
            'recheck': {'path': RECHECK_PATH.as_posix(),
                        'sha256': _sha256(RECHECK_PATH) if RECHECK_PATH.exists() else None},
        },
    )
    print(f'{len(cases)} cases → {args.out}\nmanifest → {manifest}')


if __name__ == '__main__':
    main()
