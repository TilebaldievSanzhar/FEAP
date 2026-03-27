"""OpenRouter provider — structured output via JSON Schema (OpenAI-compatible API)."""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

from feap.config import settings
from feap.llm.models import get_max_tokens, get_model, get_temperature
from feap.llm.provider import LLMProvider

T = TypeVar("T", bound=BaseModel)

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    """Recursively replace JSON Schema $ref with the referenced definition."""
    if isinstance(node, dict):
        if "$ref" in node:
            ref_name = node["$ref"].split("/")[-1]
            return _inline_refs(dict(defs[ref_name]), defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


def _strip_defaults(node: Any) -> Any:
    """Recursively remove 'default' keys — some providers reject them."""
    if isinstance(node, dict):
        return {k: _strip_defaults(v) for k, v in node.items() if k != "default"}
    if isinstance(node, list):
        return [_strip_defaults(item) for item in node]
    return node


def _prepare_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Prepare Pydantic JSON schema for OpenRouter structured output.

    - Inlines $defs/$ref
    - Strips 'default' values
    - Removes 'title'
    """
    defs = schema.get("$defs", {})
    if defs:
        schema = _inline_refs(schema, defs)
    schema = dict(schema)
    schema.pop("title", None)
    schema = _strip_defaults(schema)
    return schema


class OpenRouterProvider(LLMProvider):
    """OpenRouter API — routes to any model via OpenAI-compatible interface."""

    def __init__(self) -> None:
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                base_url=_OPENROUTER_BASE_URL,
                api_key=settings.OPENROUTER_API_KEY,
            )
        return self._client

    async def classify(
        self,
        *,
        level: int,
        system_prompt: str,
        user_message: str,
        output_model: type[T],
        model_override: str | None = None,
    ) -> T:
        client = self._get_client()
        model = model_override or get_model(level)
        max_tokens = get_max_tokens(level)
        temperature = get_temperature(level)

        schema = output_model.model_json_schema()
        schema = _prepare_schema(schema)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": output_model.__name__,
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if temperature is not None:
            kwargs["temperature"] = temperature

        response = await client.chat.completions.create(**kwargs)

        content = response.choices[0].message.content
        if not content:
            raise RuntimeError(
                f"OpenRouter returned empty response for level {level}, "
                f"model={model}, finish_reason={response.choices[0].finish_reason}"
            )

        data = json.loads(content)
        return output_model.model_validate(data)
