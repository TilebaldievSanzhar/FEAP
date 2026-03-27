"""LLM client facade — routes all calls through OpenRouter.

All cascade levels import `classify` from this module.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from feap.llm.models import resolve_model
from feap.llm.provider import LLMProvider

T = TypeVar("T", bound=BaseModel)

_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """Return the OpenRouter provider instance (lazy singleton)."""
    global _provider
    if _provider is None:
        from feap.llm.openrouter_provider import OpenRouterProvider
        _provider = OpenRouterProvider()
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
    model = model_override or resolve_model(level)
    return await get_provider().classify(
        level=level,
        system_prompt=system_prompt,
        user_message=user_message,
        output_model=output_model,
        model_override=model,
    )
