"""Test case schema for the ТН ВЭД benchmark."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class Difficulty(str, Enum):
    # ОПИ 1 applies directly, code is unambiguous
    EASY = "easy"
    # ОПИ 2-3 needed, 2+ plausible candidates
    MEDIUM = "medium"
    # Disputed in courts, composite goods, borderline sections
    HARD = "hard"


class GroundTruth(BaseModel):
    """Gold-standard classification at every hierarchy level."""

    section: str = Field(description="Roman numeral section, e.g. 'XVI'")
    group: str = Field(description="2-digit group, e.g. '85'")
    heading: str = Field(description="4-digit heading, e.g. '8509'")
    subheading: str = Field(description="6-digit subheading, e.g. '850980'")
    code_8: str = Field(description="8-digit EAEU code, e.g. '85098000'")
    code_10: str = Field(description="10-digit EAEU TN VED code used in Kyrgyzstan, e.g. '8509800000'")

    # Tariff data (filled from DB or source document)
    duty_rate: str | None = Field(default=None, description="Applicable duty rate string")
    vat_rate: str | None = Field(default=None, description="VAT rate string")

    @field_validator("heading")
    @classmethod
    def _heading_4_digits(cls, v: str) -> str:
        if not v.isdigit() or len(v) != 4:
            raise ValueError(f"Heading must be 4 digits, got '{v}'")
        return v

    @field_validator("subheading")
    @classmethod
    def _subheading_6_digits(cls, v: str) -> str:
        if not v.isdigit() or len(v) != 6:
            raise ValueError(f"Subheading must be 6 digits, got '{v}'")
        return v

    @field_validator("code_8")
    @classmethod
    def _code8_8_digits(cls, v: str) -> str:
        if not v.isdigit() or len(v) != 8:
            raise ValueError(f"code_8 must be 8 digits, got '{v}'")
        return v

    @field_validator("code_10")
    @classmethod
    def _code10_10_digits(cls, v: str) -> str:
        if not v.isdigit() or len(v) != 10:
            raise ValueError(f"code_10 must be 10 digits, got '{v}'")
        return v

    @classmethod
    def from_code10(
        cls,
        code_10: str,
        section: str,
        duty_rate: str | None = None,
        vat_rate: str | None = None,
    ) -> "GroundTruth":
        """Build a GroundTruth from a full 10-digit code."""
        raw = code_10.replace(" ", "")
        if len(raw) != 10 or not raw.isdigit():
            raise ValueError(f"Expected 10-digit numeric code, got '{code_10}'")
        return cls(
            section=section,
            group=raw[:2],
            heading=raw[:4],
            subheading=raw[:6],
            code_8=raw[:8],
            code_10=raw,
            duty_rate=duty_rate,
            vat_rate=vat_rate,
        )


class TestCase(BaseModel):
    """A single benchmark test case."""

    id: str = Field(description="Unique identifier, e.g. 'FTS-2024-0042'")
    product_description: str = Field(
        min_length=10,
        description="Raw product description as submitted by a declarant",
    )
    ground_truth: GroundTruth

    # Provenance
    source: str = Field(
        description="Source of ground truth, e.g. 'ФТС предварительное решение №10131000/12/2024/0000042'"
    )
    source_url: str | None = None

    # Classification metadata
    difficulty: Difficulty = Difficulty.MEDIUM
    opi_rules_expected: list[str] = Field(
        default_factory=list,
        description="ОПИ rules expected to be applied, e.g. ['ОПИ 1', 'ОПИ 3а']",
    )
    trap_codes: list[str] = Field(
        default_factory=list,
        description="Common wrong codes for this product — used to measure confusion",
    )

    # Scenario flags
    requires_backtrack: bool = Field(
        default=False,
        description="Correct path requires the cascade to backtrack at least once",
    )
    requires_clarification: bool = Field(
        default=False,
        description="Product description is intentionally incomplete; system should ask",
    )

    # Supplementary parameters (passed as additional_info to the classifier)
    additional_info: dict[str, str] = Field(
        default_factory=dict,
        description="Known technical parameters, e.g. {'мощность': '2.2 кВт'}",
    )
    notes: str | None = Field(
        default=None,
        description="Human-readable notes about classification nuances",
    )


class TestDataset:
    """A collection of test cases with utilities for slicing and IO."""

    def __init__(self, name: str, cases: list[TestCase], version: str = "1.0") -> None:
        self.name = name
        self.version = version
        self.cases = cases

    # ------------------------------------------------------------------ slicing

    @property
    def by_difficulty(self) -> dict[Difficulty, list[TestCase]]:
        result: dict[Difficulty, list[TestCase]] = {d: [] for d in Difficulty}
        for case in self.cases:
            result[case.difficulty].append(case)
        return result

    @property
    def by_section(self) -> dict[str, list[TestCase]]:
        result: dict[str, list[TestCase]] = {}
        for case in self.cases:
            s = case.ground_truth.section
            result.setdefault(s, []).append(case)
        return result

    def filter(self, *, difficulty: Difficulty | None = None, section: str | None = None) -> "TestDataset":
        cases = self.cases
        if difficulty is not None:
            cases = [c for c in cases if c.difficulty == difficulty]
        if section is not None:
            cases = [c for c in cases if c.ground_truth.section == section]
        return TestDataset(name=self.name, cases=cases, version=self.version)

    # ------------------------------------------------------------------ IO

    def save_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for case in self.cases:
                f.write(case.model_dump_json() + "\n")
        print(f"Saved {len(self.cases)} cases to {path}")

    @classmethod
    def load_jsonl(cls, path: Path, name: str = "") -> "TestDataset":
        cases: list[TestCase] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    cases.append(TestCase.model_validate_json(line))
        return cls(name=name or path.stem, cases=cases)

    # ------------------------------------------------------------------ summary

    def summary(self) -> str:
        by_d = self.by_difficulty
        by_s = self.by_section
        trap_count = sum(1 for c in self.cases if c.trap_codes)
        backtrack_count = sum(1 for c in self.cases if c.requires_backtrack)
        clarify_count = sum(1 for c in self.cases if c.requires_clarification)

        lines = [
            f"═══ Dataset: {self.name}  v{self.version} ═══",
            f"Total cases : {len(self.cases)}",
            "By difficulty:",
            *[f"  {d.value:8s}: {len(by_d[d])}" for d in Difficulty],
            f"Sections    : {len(by_s)}  ({', '.join(sorted(by_s))})",
            f"With traps  : {trap_count}",
            f"Backtrack   : {backtrack_count}",
            f"Clarify     : {clarify_count}",
        ]
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self.cases)

    def __iter__(self):
        return iter(self.cases)
