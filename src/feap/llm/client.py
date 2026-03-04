"""LLM client facade — delegates to the active provider (Anthropic or Gemini)."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from feap.config import settings
from feap.llm.provider import LLMProvider

T = TypeVar("T", bound=BaseModel)

_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """Return the singleton LLM provider based on LLM_PROVIDER setting."""
    global _provider
    if _provider is None:
        if settings.LLM_PROVIDER == "google":
            from feap.llm.gemini_provider import GeminiProvider

            _provider = GeminiProvider()
        else:
            from feap.llm.anthropic_provider import AnthropicProvider

            _provider = AnthropicProvider()
    return _provider


async def classify(
    *,
    level: int,
    system_prompt: str,
    user_message: str,
    output_model: type[T],
    model_override: str | None = None,
) -> T:
    """Backward-compatible entry point — all cascade levels import this."""
    return await get_provider().classify(
        level=level,
        system_prompt=system_prompt,
        user_message=user_message,
        output_model=output_model,
        model_override=model_override,
    )
