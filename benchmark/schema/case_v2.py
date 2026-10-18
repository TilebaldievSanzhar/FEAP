"""Benchmark case schema v2: a product (and later a shipment) with layered gold.

v1 (test_case.py) scores a ten-digit code. v2 scores what the declarant would
get: the code, the money it implies, the permits, and later the whole draft
declaration. A case carries only the gold layers its source can support — an
EEC classification decision gives a code; a released declaration also gives
payments and permits. Stage metrics use the cases that have the layer.

Who stands behind a label (tier) and how far it was checked (verification)
are separate: a synthetic description can be source-reviewed, an official
decision can be outdated by a new nomenclature edition.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import Counter
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

from benchmark.schema.test_case import Difficulty, GroundTruth, TestCase, TestDataset

SCHEMA_VERSION = '2.0'

#: Heading, subheading, and the eight-, nine- and ten-digit levels of ТН ВЭД ЕАЭС.
_CODE = re.compile(r'^(?:\d{4}|\d{6}|\d{8,10})$')

#: First two digits of a code → section (Roman numeral).
_SECTION_BOUNDS: tuple[tuple[int, str], ...] = (
    (5, 'I'), (14, 'II'), (15, 'III'), (24, 'IV'), (27, 'V'), (38, 'VI'), (40, 'VII'),
    (43, 'VIII'), (46, 'IX'), (49, 'X'), (63, 'XI'), (67, 'XII'), (70, 'XIII'), (71, 'XIV'),
    (83, 'XV'), (85, 'XVI'), (89, 'XVII'), (92, 'XVIII'), (93, 'XIX'), (96, 'XX'), (97, 'XXI'),
)
SECTIONS: tuple[str, ...] = tuple(section for _, section in _SECTION_BOUNDS)


def section_of(code: str) -> str | None:
    if len(code) < 2 or not code[:2].isdigit():
        return None
    group = int(code[:2])
    return next((section for upper, section in _SECTION_BOUNDS if group <= upper), None)


def _check_code(code: str) -> str:
    """Heading to ten digits — as precise as the source is."""
    if not _CODE.match(code):
        raise ValueError(f'Expected a code of 4, 6, 8, 9 or 10 digits, got {code!r}')
    return code


class Track(str, Enum):
    PASSPORT = 'passport'  # product: description / photo → code → payments → measures
    DOSSIER = 'dossier'    # shipment: documents → draft declaration (phase 2)


class GoldTier(str, Enum):
    """Who stands behind the gold, most authoritative first."""

    NORMATIVE = 'A'    # EEC classification decision, preliminary decision, court ruling
    OPERATIONAL = 'B'  # declaration released by customs (silver until post-clearance audit)
    EXPERT = 'C'       # labelled by a licensed declarant
    SYNTHETIC = 'D'    # description and label written by the team


class Verification(str, Enum):
    OFFICIAL = 'official'                # the authority set this code for this product
    EXPERT_REVIEWED = 'expert_reviewed'
    SOURCE_REVIEWED = 'source_reviewed'  # team check against official texts, no expert
    UNVERIFIED = 'unverified'
    QUARANTINED = 'quarantined'          # the input does not support the exact code
    DISPUTED = 'disputed'                # several codes defensible, facts missing


#: Not counted in headline numbers: the gold itself is not established.
NOT_SCORED: frozenset[Verification] = frozenset({Verification.QUARANTINED, Verification.DISPUTED})


class Sufficiency(str, Enum):
    """Does the input fix the gold code at its precision?"""

    SUFFICIENT = 'sufficient'
    INSUFFICIENT = 'insufficient'  # the right behaviour is a question or a flag
    UNKNOWN = 'unknown'            # not established (e.g. unaudited synthetic labels)


class SourceRef(BaseModel):
    title: str
    url: str | None = None
    pages: list[int] = Field(default_factory=list, description='One-based PDF pages')
    retrieved_on: dt.date | None = None
    sha256: str | None = None


class Provenance(BaseModel):
    kind: Literal[
        'eec_decision', 'preliminary_decision', 'court_ruling',
        'partner_declaration', 'expert_label', 'synthetic',
    ]
    reference: str = Field(description='Decision number, document or author, as written')
    sources: list[SourceRef] = Field(default_factory=list)


class GoldReview(BaseModel):
    """One decision about a gold label. History is appended, never rewritten."""

    on: dt.date
    by: str = Field(description="'team source review', an expert's role, …")
    disposition: Literal['confirmed', 'corrected', 'quarantined', 'disputed']
    previous_code: str | None = Field(
        None, validation_alias=AliasChoices('previous_code', 'previous_code_10'),
    )
    reason: str
    sources: list[SourceRef] = Field(default_factory=list)


class ConditionalCode(BaseModel):
    code: str = Field(validation_alias=AliasChoices('code', 'code_10'))
    condition: str = Field(description='The fact that would make this code right')

    @field_validator('code')
    @classmethod
    def _code(cls, v: str) -> str:
        return _check_code(v)


class ClassificationGold(BaseModel):
    code: str = Field(
        validation_alias=AliasChoices('code', 'code_10'),
        description='As precise as the source: an EEC decision may fix only the heading',
    )
    acceptable: list[str] = Field(
        default_factory=list, description='Other defensible codes, each justified in reviews',
    )
    conditional: list[ConditionalCode] = Field(default_factory=list)
    verification: Verification
    sufficiency: Sufficiency
    missing_facts: list[str] = Field(
        default_factory=list, description='What the input lacks for the exact code',
    )
    trap_codes: list[str] = Field(default_factory=list)
    opi_expected: list[str] = Field(default_factory=list)
    requires_backtrack: bool = False
    reviews: list[GoldReview] = Field(default_factory=list)

    @field_validator('code')
    @classmethod
    def _code(cls, v: str) -> str:
        return _check_code(v)

    @field_validator('acceptable')
    @classmethod
    def _acceptable(cls, v: list[str]) -> list[str]:
        return [_check_code(c) for c in v]

    @property
    def scored(self) -> bool:
        return self.verification not in NOT_SCORED

    @property
    def digits(self) -> int:
        return len(self.code)

    def accepts(self, predicted: str | None) -> bool:
        """A ten-digit answer is right if it starts with the gold (or an acceptable) code."""
        return bool(predicted) and any(
            predicted.startswith(code) for code in (self.code, *self.acceptable))


class Lot(BaseModel):
    """Shipment parameters under which the payments of two codes are compared."""

    price: Decimal = Field(gt=0, description='Lot value in currency')
    currency: str = Field('USD', pattern=r'^[A-Z]{3}$')
    incoterms: str = 'CPT'
    origin_country: str | None = 'CN'
    manufacturer: str | None = None
    qty: Decimal | None = Field(None, gt=0)
    qty_unit: str | None = None
    net_kg: Decimal | None = Field(None, ge=0)
    gross_kg: Decimal | None = Field(None, ge=0)
    supp_qty: Decimal | None = Field(None, ge=0)
    supp_unit: str | None = None
    standard: bool = Field(
        True, description='Benchmark-wide default, not facts of this product',
    )


class PassportInput(BaseModel):
    description: str = Field(min_length=3)
    additional_info: dict[str, str] = Field(default_factory=dict)
    fidelity: Literal['full', 'invoice_line', 'photo'] = 'full'
    language: str = 'ru'


class GoldLayers(BaseModel):
    classification: ClassificationGold | None = None


class CaseV2(BaseModel):
    schema_version: Literal['2.0'] = SCHEMA_VERSION
    id: str = Field(min_length=1)
    track: Track
    split: Literal['regression', 'control', 'dev', 'test']
    tier: GoldTier
    provenance: Provenance
    as_of_date: dt.date | None = Field(
        None, description='Date of the source event; rates and measures are taken on it',
    )
    nomenclature: str = 'ТН ВЭД ЕАЭС 2022'
    difficulty: Literal['easy', 'medium', 'hard'] | None = None
    tags: list[str] = Field(default_factory=list)
    input: PassportInput
    lot: Lot | None = Field(None, description='None: the scorer applies its standard lot')
    gold: GoldLayers
    notes: str | None = None

    @model_validator(mode='after')
    def _track(self) -> CaseV2:
        if self.track is Track.DOSSIER:
            raise ValueError('Dossier cases are not supported yet (phase 2)')
        return self

    @property
    def section(self) -> str | None:
        gold = self.gold.classification
        return section_of(gold.code) if gold else None


# ── IO ──────────────────────────────────────────────────────────────────────

def load_cases(path: Path) -> list[CaseV2]:
    with path.open(encoding='utf-8') as f:
        cases = [CaseV2.model_validate_json(line) for line in f if line.strip()]
    ids = [c.id for c in cases]
    if len(set(ids)) != len(ids):
        duplicates = sorted(i for i, n in Counter(ids).items() if n > 1)
        raise ValueError(f'Duplicate case ids in {path}: {duplicates}')
    return cases


def to_v1(case: CaseV2) -> TestCase:
    """A v2 case as a v1 TestCase, so the existing runners can classify it.

    v1 needs ten digits: a shorter gold code is padded with zeros, so v1 metrics on
    v2 sets are not meaningful — score them with score_v2.
    """
    gold = case.gold.classification
    if gold is None:
        raise ValueError(f'{case.id}: no classification gold')
    return TestCase(
        id=case.id,
        product_description=case.input.description,
        ground_truth=GroundTruth.from_code10(gold.code.ljust(10, '0'), case.section or '?'),
        source=case.provenance.reference,
        difficulty=Difficulty(case.difficulty) if case.difficulty else Difficulty.UNKNOWN,
        opi_rules_expected=gold.opi_expected,
        trap_codes=gold.trap_codes,
        requires_backtrack=gold.requires_backtrack,
        additional_info=case.input.additional_info,
        notes=case.notes,
    )


def is_v2_file(path: Path) -> bool:
    with path.open(encoding='utf-8') as f:
        first = next((line for line in f if line.strip()), '')
    return bool(first) and json.loads(first).get('schema_version') == SCHEMA_VERSION


def load_dataset(path: Path) -> TestDataset:
    """v1 or v2 JSONL as a v1 TestDataset (v2 cases converted with to_v1)."""
    if is_v2_file(path):
        return TestDataset(name=path.stem, cases=[to_v1(c) for c in load_cases(path)])
    return TestDataset.load_jsonl(path)


def save_cases(cases: list[CaseV2], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='\n') as f:
        for case in cases:
            f.write(case.model_dump_json(exclude_none=True) + '\n')


def write_manifest(path: Path, cases: list[CaseV2], *, purpose: str, built_from: dict) -> Path:
    """Manifest next to the JSONL: hash, composition, provenance of the build."""
    manifest_path = path.with_suffix('.manifest.json')
    gold = [c.gold.classification for c in cases if c.gold.classification]
    manifest = {
        'schema_version': SCHEMA_VERSION,
        'created_at': dt.datetime.now(dt.UTC).isoformat(),
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'n': len(cases),
        'purpose': purpose,
        'built_from': built_from,
        'by_track': dict(Counter(c.track.value for c in cases)),
        'by_split': dict(Counter(c.split for c in cases)),
        'by_tier': dict(Counter(c.tier.value for c in cases)),
        'by_verification': dict(Counter(g.verification.value for g in gold)),
        'by_sufficiency': dict(Counter(g.sufficiency.value for g in gold)),
        'by_gold_digits': dict(sorted(Counter(g.digits for g in gold).items())),
        'scored': sum(g.scored for g in gold),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                             encoding='utf-8')
    return manifest_path
