"""Short variants of cases: the first sentence of the description, same gold.

Users type «насос центробежный», not a two-page specification. A short variant
keeps the gold code of the official decision and gives only the first sentence
of its description (without «Товар представляет собой»), cut to 160 characters
at a word. It approximates an invoice line, it is not one. The question is
whether the system asks or flags instead of guessing silently: sufficiency is
unknown, so a question counts as a safe stop, never as an error.

Run:
    python -m benchmark.scripts.build_short_variants \\
        --cases benchmark/data/cases/v2/eaeu_pd_test400.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

from benchmark.schema.case_v2 import CaseV2, Sufficiency, load_cases, save_cases, write_manifest

MAX_CHARS = 160
MIN_CHARS = 25

_SENTENCE_END = re.compile(r'(?<=[.!?])\s+(?=[А-ЯЁA-Z«"(])')
_LEAD = re.compile(r'^(?:товар\s*(?:[-–—:]\s*|представляет\s+собой\s+))', re.IGNORECASE)


def short_description(description: str) -> str:
    sentences = _SENTENCE_END.split(description.strip())
    text = _LEAD.sub('', sentences[0]).strip()
    for extra in sentences[1:]:
        if len(text) >= MIN_CHARS:
            break
        text = f'{text} {extra}'
    if len(text) > MAX_CHARS:
        cut = text[:MAX_CHARS]
        text = cut[:cut.rfind(' ')] if ' ' in cut else cut
    text = text.rstrip(' ,;:-–—')
    return text[:1].upper() + text[1:]


def short_case(case: CaseV2) -> CaseV2:
    gold = case.gold.classification.model_copy(update={'sufficiency': Sufficiency.UNKNOWN})
    return case.model_copy(update={
        'id': f'{case.id}-S',
        'tags': sorted({*case.tags, 'short_variant'}),
        'input': case.input.model_copy(update={
            'description': short_description(case.input.description), 'fidelity': 'invoice_line',
        }),
        'gold': case.gold.model_copy(update={'classification': gold}),
        'notes': f'Короткий вариант {case.id}: первое предложение описания.'
                 + (f' {case.notes}' if case.notes else ''),
    })


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--out', type=Path, default=None, help='Default: <cases>_short.jsonl')
    args = p.parse_args()
    out = args.out or args.cases.with_name(args.cases.stem + '_short.jsonl')
    source = load_cases(args.cases)
    cases = [short_case(c) for c in source]
    for case in cases:
        CaseV2.model_validate(case.model_dump())  # min length and the rest of the schema
    save_cases(cases, out)
    write_manifest(out, cases, purpose=(
        f'Short variants of {args.cases.name} (same split, locked if the source is): '
        'first sentence of the official description, same gold code.'),
        built_from={'cases': {'path': args.cases.as_posix(),
                              'sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest()},
                    'rule': f'first sentence, lead «Товар представляет собой» removed, '
                            f'≤{MAX_CHARS} characters'})
    lengths = sorted(len(c.input.description) for c in cases)
    print(f'{len(cases)} → {out}; length median {lengths[len(lengths) // 2]}, '
          f'min {lengths[0]}, max {lengths[-1]}')


if __name__ == '__main__':
    main()
