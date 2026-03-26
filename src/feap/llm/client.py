"""LLM client facade — routes each call to the correct provider (Anthropic or Gemini).

Supports mixed-provider cascades: e.g. L4 on Anthropic, rest on Gemini.
Provider is resolved per-call via LEVEL{N}_MODEL env vars or LLM_PROVIDER fallback.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from feap.llm.models import resolve_model
from feap.llm.provider import LLMProvider

T = TypeVar("T", bound=BaseModel)

_providers: dict[str, LLMProvider] = {}


def get_provider(provider_name: str) -> LLMProvider:
    """Return the LLM provider instance (lazy singleton per provider)."""
    if provider_name not in _providers:
        if provider_name == "google":
            from feap.llm.gemini_provider import GeminiProvider
            _providers[provider_name] = GeminiProvider()
        else:
            from feap.llm.anthropic_provider import AnthropicProvider
            _providers[provider_name] = AnthropicProvider()
    return _providers[provider_name]


async def classify(
    *,
    level: int,
    system_prompt: str,
    user_message: str,
    output_model: type[T],
    model_override: str | None = None,
) -> T:
    """Backward-compatible entry point — all cascade levels import this."""
    provider_name, model = resolve_model(level)
    return await get_provider(provider_name).classify(
        level=level,
        system_prompt=system_prompt,
        user_message=user_message,
        output_model=output_model,
        model_override=model_override or model,
    )
