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

        # Results per level
        self.section: str | None = None
        self.group: str | None = None
        self.position: str | None = None
        self.subposition: str | None = None
        self.code_10: str | None = None

        # Missing info
        self.missing_parameters: list[str] = []
        self.clarifying_question: str | None = None

    def add_decision(self, entry: DecisionChainEntry) -> None:
        self.chain.append(entry)

    def add_exclusion(self, level: int, code: str) -> None:
        self.excluded.setdefault(level, []).append(code)

    def get_excluded(self, level: int) -> list[str]:
        return self.excluded.get(level, [])


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
