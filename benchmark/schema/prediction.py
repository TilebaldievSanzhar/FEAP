"""Prediction schema: classifier output paired with a test case."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class CasePrediction(BaseModel):
    """Classifier output for one test case."""

    case_id: str
    status: str  # "completed" | "needs_input" | "error"

    # Extracted predictions (None if not reached that level)
    predicted_section: str | None = None
    predicted_group: str | None = None       # 2 digits
    predicted_heading: str | None = None     # 4 digits
    predicted_subheading: str | None = None  # 6 digits
    predicted_code_8: str | None = None
    predicted_code_10: str | None = None

    predicted_confidence: float | None = None
    predicted_opi: list[str] = []

    # Error / partial
    error_message: str | None = None
    partial_code: str | None = None
    determined_to_level: int | None = None
    missing_parameters: list[str] = []

    # Performance
    latency_ms: float = 0.0
    ran_at: str = ""  # ISO datetime string

    # Raw response stored as dict for full auditability
    raw_response: dict[str, Any] = {}

    @classmethod
    def from_response(
        cls,
        case_id: str,
        response_dict: dict[str, Any],
        latency_ms: float = 0.0,
    ) -> "CasePrediction":
        """Build from a serialised ClassifyResponse dict."""
        status = response_dict.get("status", "error")

        pred = cls(
            case_id=case_id,
            status=status,
            latency_ms=latency_ms,
            ran_at=datetime.now(UTC).isoformat(),
            raw_response=response_dict,
        )

        result = response_dict.get("result")
        if result:
            raw = result.get("final_code", "").replace(" ", "")
            pred.predicted_code_10 = raw if len(raw) == 10 else None
            pred.predicted_code_8 = raw[:8] if len(raw) >= 8 else None
            pred.predicted_heading = raw[:4] if len(raw) >= 4 else None
            pred.predicted_subheading = raw[:6] if len(raw) >= 6 else None
            pred.predicted_group = raw[:2] if len(raw) >= 2 else None
            pred.predicted_confidence = result.get("confidence")
            pred.predicted_opi = result.get("opi_applied", [])

            chain = result.get("classification_chain", {})
            section_entry = chain.get("section", {})
            pred.predicted_section = section_entry.get("code")

        partial = response_dict.get("partial")
        if partial:
            pred.partial_code = partial.get("partial_code")
            pred.determined_to_level = partial.get("determined_to_level")
            pred.missing_parameters = partial.get("missing_parameters", [])

        if status == "error":
            pred.error_message = response_dict.get("error")

        return pred


@dataclass
class CaseResult:
    """Comparison result for one test case."""

    case_id: str
    difficulty: str
    section: str  # ground truth section

    # Match booleans at each hierarchy level
    section_match: bool = False
    group_match: bool = False
    heading_match: bool = False      # 4 digits — primary KPI
    subheading_match: bool = False   # 6 digits — determines duty rate
    code_8_match: bool = False
    code_10_match: bool = False

    # Hierarchical weighted score [0, 1]
    hierarchical_score: float = 0.0

    # Where in the hierarchy the first error occurred (None = correct all way)
    first_error_level: str | None = None  # "section"|"group"|"heading"|"subheading"|"code_8"|"code_10"

    # Whether the predicted code is one of the known trap codes
    fell_into_trap: bool = False

    # Confidence from the classifier
    confidence: float | None = None

    # System behaviour flags
    status: str = "completed"
    correctly_stopped: bool = False   # required_clarification and system stopped
    wrongly_stopped: bool = False     # did NOT require clarification but system stopped

    # Financial
    duty_rate_correct: bool | None = None  # None = not evaluable

    latency_ms: float = 0.0


def save_predictions(predictions: list[CasePrediction], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for p in predictions:
            f.write(p.model_dump_json() + "\n")


def load_predictions(path: Path) -> list[CasePrediction]:
    preds: list[CasePrediction] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                preds.append(CasePrediction.model_validate_json(line))
    return preds
