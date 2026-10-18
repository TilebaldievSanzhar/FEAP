"""Payment scenarios: the money stage checked against an independent calculation.

A scenario fixes a lot, a date and the rates its author read in the official
sources ('declared'). The oracle (metrics/payments_oracle.py) recomputes the
payments from the declared rates with plain formulas and no product code; the
runner compares them with calc.run_calc on the current reference data. A
mismatch is attributed: another rate in the engine means reference data,
the same rate with another amount means the engine.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from benchmark.schema.case_v2 import SourceRef
from feap.calc.schemas import CalcIn


class DeclaredDuty(BaseModel):
    text: str = Field(description='The rate as the engine prints it in graph 47')
    pct: Decimal | None = None
    min_eur_per_kg: Decimal | None = Field(None, description="'…%, но не менее X EUR за 1 кг'")
    eur_per_pair: Decimal | None = Field(None, description="'X EUR за 1 пар'")

    @model_validator(mode='after')
    def _one_form(self) -> DeclaredDuty:
        """Ad valorem, ad valorem «не менее» per kg, or per pair — the forms the oracle knows."""
        if (self.pct is None) == (self.eur_per_pair is None):
            raise ValueError('Declare either pct (optionally with min_eur_per_kg) or eur_per_pair')
        if self.min_eur_per_kg is not None and self.pct is None:
            raise ValueError("'Не менее' needs the ad valorem part")
        return self


class Declared(BaseModel):
    """Everything the oracle may use. Nothing here is read from the database."""

    fx: dict[str, Decimal] = Field(description='NBKR rate on rate_date, сом per unit')
    duty: DeclaredDuty
    vat_pct: Decimal
    excise_som_per_litre: Decimal | None = None
    antidumping_pct: Decimal | None = None
    vat_base_includes_remedies: bool = False
    fee_pct: Decimal = Decimal('0.4')
    fee_min_kgs: Decimal = Decimal(500)       # 5 РП × 100 сом
    fee_max_kgs: Decimal = Decimal(250000)    # 2500 РП × 100 сом


class CalcScenario(BaseModel):
    id: str
    title: str
    checks: str = Field(description='What the scenario is about, in one line')
    calc: CalcIn
    declared: Declared | None = Field(
        None, description='None: only the status and missing data are checked',
    )
    expected_status: Literal['ready', 'has_errors', 'needs_input']
    expected_missing: list[str] = Field(
        default_factory=list, description='Substrings of missing_parameters',
    )
    sources: list[SourceRef] = Field(default_factory=list)
    verification: Literal['source_reviewed', 'snapshot_locked'] = Field(
        description='source_reviewed: rates read in official texts; snapshot_locked: taken '
                    'from the reference data on the build date and not re-verified',
    )
    note: str | None = None


def load_scenarios(path: Path) -> list[CalcScenario]:
    with path.open(encoding='utf-8') as f:
        scenarios = [CalcScenario.model_validate_json(line) for line in f if line.strip()]
    ids = Counter(s.id for s in scenarios)
    duplicates = sorted(i for i, n in ids.items() if n > 1)
    if duplicates:
        raise ValueError(f'Duplicate scenario ids in {path}: {duplicates}')
    return scenarios


def save_scenarios(scenarios: list[CalcScenario], path: Path, *, purpose: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='\n') as f:
        for scenario in scenarios:
            f.write(scenario.model_dump_json(exclude_none=True) + '\n')
    manifest_path = path.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps({
        'created_at': dt.datetime.now(dt.UTC).isoformat(),
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'n': len(scenarios),
        'purpose': purpose,
        'by_verification': dict(Counter(s.verification for s in scenarios)),
        'by_status': dict(Counter(s.expected_status for s in scenarios)),
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest_path
