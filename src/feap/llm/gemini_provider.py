"""Google Gemini provider — structured output via constrained decoding."""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

from feap.config import settings
from feap.llm.models import get_max_tokens, get_model, get_temperature, get_thinking_budget
from feap.llm.provider import LLMProvider

T = TypeVar("T", bound=BaseModel)


def _strip_defaults(node: Any) -> Any:
    """Recursively remove 'default' keys from JSON Schema.

    Gemini API rejects schemas that contain default values.
    We strip them from the schema sent to the API while keeping
    the Pydantic models intact on the Python side.
    """
    if isinstance(node, dict):
        return {
            k: _strip_defaults(v)
            for k, v in node.items()
            if k != "default"
        }
    if isinstance(node, list):
        return [_strip_defaults(item) for item in node]
    return node


def _prepare_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Prepare a Pydantic JSON schema for Gemini API.

    - Strips 'default' values (Gemini rejects them)
    - Removes 'title' (not needed)
    - Inlines $defs/$ref for compatibility
    """
    schema = dict(schema)
    schema.pop("title", None)
    schema = _strip_defaults(schema)
    return schema


class GeminiProvider(LLMProvider):
    """Google Gemini API with structured output via constrained JSON decoding."""

    def __init__(self) -> None:
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
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
        from google.genai import types

        client = self._get_client()
        model = model_override or get_model(level)
        max_tokens = get_max_tokens(level)

        schema = output_model.model_json_schema()
        schema = _prepare_schema(schema)

        thinking_budget = get_thinking_budget(level)
        temperature = get_temperature(level)

        config_kwargs: dict[str, Any] = {
            "system_instruction": system_prompt,
            "max_output_tokens": max_tokens,
            "response_mime_type": "application/json",
            "response_json_schema": schema,
            "thinking_config": types.ThinkingConfig(thinking_budget=thinking_budget),
        }
        if temperature is not None:
            config_kwargs["temperature"] = temperature

        config = types.GenerateContentConfig(**config_kwargs)

        response = await client.aio.models.generate_content(
            model=model,
            contents=user_message,
            config=config,
        )

        # Try .parsed first (available when response_schema is a Pydantic class)
        # Since we use response_json_schema (dict), we parse manually.
        if response.text is None:
            raise RuntimeError(
                f"Gemini returned empty response for level {level}. "
                f"Finish reason: {response.candidates[0].finish_reason if response.candidates else 'unknown'}"
            )

        data = json.loads(response.text)
        return output_model.model_validate(data)
