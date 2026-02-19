"""Anthropic API client wrapper with Structured Outputs via forced tool use."""

from __future__ import annotations

from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel

from feap.config import settings
from feap.llm.models import get_max_tokens, get_model

T = TypeVar("T", bound=BaseModel)

_client: anthropic.AsyncAnthropic | None = None

# Neutral tool name — avoid words like "output"/"result" that make the LLM
# wrap the response under a matching key instead of returning flat fields.
_TOOL_NAME = "fill_form"


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    """Recursively replace JSON Schema $ref with the referenced definition."""
    if isinstance(node, dict):
        if "$ref" in node:
            ref_name = node["$ref"].split("/")[-1]
            return _inline_refs(dict(defs[ref_name]), defs)
        return {
            k: _inline_refs(v, defs)
            for k, v in node.items()
            if k != "$defs"
        }
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


def _flatten_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline all $defs/$ref so Anthropic receives a flat, unambiguous schema."""
    defs = schema.get("$defs", {})
    if not defs:
        return schema
    return _inline_refs(schema, defs)


async def classify(
    *,
    level: int,
    system_prompt: str,
    user_message: str,
    output_model: type[T],
    model_override: str | None = None,
) -> T:
    """Call Anthropic API with forced tool use for structured output.

    Uses tools + tool_choice to guarantee Pydantic model output.
    Schema $defs are inlined so the model cannot confuse nested refs with
    top-level field names.
    """
    client = get_client()
    model = model_override or get_model(level)
    max_tokens = get_max_tokens(level)

    schema = output_model.model_json_schema()
    schema.pop("title", None)
    schema = _flatten_schema(schema)

    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        tools=[
            {
                "name": _TOOL_NAME,
                "description": f"Fill in the required fields for {output_model.__name__}",
                "input_schema": schema,
            }
        ],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == _TOOL_NAME:
            return output_model.model_validate(block.input)

    raise RuntimeError(f"No tool_use block in response from level {level}")
