"""Base class for cascade levels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from feap.schemas.common import DecisionChainEntry


class CascadeState:
    """Mutable state passed through the cascade."""

    def __init__(self, product_description: str):
        self.product_description: str = product_description
        self.product_card: dict | None = None
        self.chain: list[DecisionChainEntry] = []
        self.excluded: dict[int, list[str]] = {}  # level -> excluded codes
        self.excluded_reasons: dict[int, list[dict]] = {}  # level -> [{code, reason}]

        # Results per level
        self.section: str | None = None
        self.group: str | None = None
        self.position: str | None = None
        self.subposition: str | None = None
        self.code_10: str | None = None

        # Missing info
        self.missing_parameters: list[str] = []
        self.clarifying_question: str | None = None

        # Session resumption
        self.stopped_at_level: int | None = None
        self.answer_count: int = 0

    def add_decision(self, entry: DecisionChainEntry) -> None:
        self.chain.append(entry)

    def add_exclusion(self, level: int, code: str, reason: str = "") -> None:
        self.excluded.setdefault(level, []).append(code)
        if reason:
            self.excluded_reasons.setdefault(level, []).append(
                {"code": code, "reason": reason}
            )

    def get_excluded(self, level: int) -> list[str]:
        return self.excluded.get(level, [])

    def get_excluded_reasons(self, level: int) -> list[dict]:
        return self.excluded_reasons.get(level, [])

    def to_dict(self) -> dict:
        """Serialize state to a JSON-compatible dict for DB storage."""
        return {
            "product_description": self.product_description,
            "product_card": self.product_card,
            "chain": [e.model_dump() for e in self.chain],
            "excluded": {str(k): v for k, v in self.excluded.items()},
            "excluded_reasons": {str(k): v for k, v in self.excluded_reasons.items()},
            "section": self.section,
            "group": self.group,
            "position": self.position,
            "subposition": self.subposition,
            "code_10": self.code_10,
            "missing_parameters": self.missing_parameters,
            "clarifying_question": self.clarifying_question,
            "stopped_at_level": self.stopped_at_level,
            "answer_count": self.answer_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CascadeState:
        """Restore state from a serialized dict."""
        state = cls(data["product_description"])
        state.product_card = data.get("product_card")
        state.chain = [
            DecisionChainEntry(**e) for e in data.get("chain", [])
        ]
        state.excluded = {
            int(k): v for k, v in data.get("excluded", {}).items()
        }
        state.excluded_reasons = {
            int(k): v for k, v in data.get("excluded_reasons", {}).items()
        }
        state.section = data.get("section")
        state.group = data.get("group")
        state.position = data.get("position")
        state.subposition = data.get("subposition")
        state.code_10 = data.get("code_10")
        state.missing_parameters = data.get("missing_parameters", [])
        state.clarifying_question = data.get("clarifying_question")
        state.stopped_at_level = data.get("stopped_at_level")
        state.answer_count = data.get("answer_count", 0)
        return state


class BaseCascadeLevel(ABC):
    """Abstract base for each cascade level."""

    level: int

    @abstractmethod
    async def run(self, state: CascadeState, db: AsyncSession) -> Any:
        """Execute this cascade level and return the Pydantic result."""
        ...

    @abstractmethod
    def get_output_model(self) -> type[BaseModel]:
        """Return the Pydantic model class for this level's output."""
        ...
