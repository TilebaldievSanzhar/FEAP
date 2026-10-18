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

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = '2.0'

_CODE10 = re.compile(r'^\d{10}$')

#: First two digits of a code → section (Roman numeral).
_SECTION_BOUNDS: tuple[tuple[int, str], ...] = (
    (5, 'I'), (14, 'II'), (15, 'III'), (24, 'IV'), (27, 'V'), (38, 'VI'), (40, 'VII'),
    (43, 'VIII'), (46, 'IX'), (49, 'X'), (63, 'XI'), (67, 'XII'), (70, 'XIII'), (71, 'XIV'),
    (83, 'XV'), (85, 'XVI'), (89, 'XVII'), (92, 'XVIII'), (93, 'XIX'), (96, 'XX'), (97, 'XXI'),
)


def section_of(code: str) -> str | None:
    if len(code) < 2 or not code[:2].isdigit():
        return None
    group = int(code[:2])
    return next((section for upper, section in _SECTION_BOUNDS if group <= upper), None)


def _check_code10(code: str) -> str:
    if not _CODE10.match(code):
        raise ValueError(f'Expected a ten-digit code, got {code!r}')
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
    """Does the input fix the gold code at ten digits?"""

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
    previous_code_10: str | None = None
    reason: str
    sources: list[SourceRef] = Field(default_factory=list)


class ConditionalCode(BaseModel):
    code_10: str
    condition: str = Field(description='The fact that would make this code right')

    @field_validator('code_10')
    @classmethod
    def _code(cls, v: str) -> str:
        return _check_code10(v)


class ClassificationGold(BaseModel):
    code_10: str
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

    @field_validator('code_10')
    @classmethod
    def _code(cls, v: str) -> str:
        return _check_code10(v)

    @field_validator('acceptable')
    @classmethod
    def _acceptable(cls, v: list[str]) -> list[str]:
        return [_check_code10(c) for c in v]

    @property
    def scored(self) -> bool:
        return self.verification not in NOT_SCORED

    def accepts(self, code: str | None) -> bool:
        return code is not None and (code == self.code_10 or code in self.acceptable)


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
        return section_of(gold.code_10) if gold else None


# ── IO ──────────────────────────────────────────────────────────────────────

def load_cases(path: Path) -> list[CaseV2]:
    with path.open(encoding='utf-8') as f:
        cases = [CaseV2.model_validate_json(line) for line in f if line.strip()]
    ids = [c.id for c in cases]
    if len(set(ids)) != len(ids):
        duplicates = sorted(i for i, n in Counter(ids).items() if n > 1)
        raise ValueError(f'Duplicate case ids in {path}: {duplicates}')
    return cases


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
        'scored': sum(g.scored for g in gold),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                             encoding='utf-8')
    return manifest_path
