"""Anthropic API client wrapper with Structured Outputs."""

from __future__ import annotations

from typing import TypeVar

import anthropic
from pydantic import BaseModel

from feap.config import settings
from feap.llm.models import get_max_tokens, get_model

T = TypeVar("T", bound=BaseModel)

_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


async def classify(
    *,
    level: int,
    system_prompt: str,
    user_message: str,
    output_model: type[T],
    model_override: str | None = None,
) -> T:
    """Call Anthropic API with Structured Outputs.

    Uses client.messages.parse() to get guaranteed Pydantic model output.
    """
    client = get_client()
    model = model_override or get_model(level)
    max_tokens = get_max_tokens(level)

    response = await client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        output_format=output_model,
    )

    return response.parsed_output
