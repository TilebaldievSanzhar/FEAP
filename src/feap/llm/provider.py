"""Abstract LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(ABC):
    """Base class for LLM providers (Anthropic, Google Gemini, etc.)."""

    @abstractmethod
    async def classify(
        self,
        *,
        level: int,
        system_prompt: str,
        user_message: str,
        output_model: type[T],
        model_override: str | None = None,
    ) -> T:
        """Generate a structured response matching the Pydantic output_model."""
        ...
