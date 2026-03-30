"""OpenRouter provider — structured output via JSON Schema (OpenAI-compatible API).

Supports two structured output strategies:
1. json_schema strict — grammar-constrained decoding (Anthropic, OpenAI, DeepSeek, etc.)
2. json_object + schema in prompt — for models without strict support (MiniMax, etc.)

See docs/structured_output_providers.md for details.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from feap.config import settings
from feap.llm.models import get_max_tokens, get_model, get_temperature
from feap.llm.provider import LLMProvider

T = TypeVar("T", bound=BaseModel)

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

logger = logging.getLogger(__name__)

# Models that require json_object + schema-in-prompt strategy.
# These models don't support json_schema strict via OpenRouter providers.
# Pattern match: if the resolved model ID *starts with* any of these prefixes,
# the prompt-based strategy is used.
_PROMPT_SCHEMA_MODEL_PREFIXES: tuple[str, ...] = (
    "minimax/minimax-m2",
)

# Models with mandatory reasoning (thinking) that cannot be disabled.
# For these models, we must NOT send reasoning: {effort: "none"}.
_MANDATORY_REASONING_PREFIXES: tuple[str, ...] = (
    "minimax/minimax-m2",
)

# Max retries for truncated responses (repetition loops).
_MAX_RETRIES = 3

# Temperature bump on retry to break deterministic repetition loops.
_RETRY_TEMPERATURE = 0.3

_PROMPT_SCHEMA_INSTRUCTION = (
    "Верни JSON-объект, соответствующий следующей JSON Schema.\n"
    "ВАЖНО: Верни ПЛОСКИЙ объект с полями из 'properties', "
    "НЕ оборачивай в {\"properties\": {...}}.\n"
    "Пример правильного ответа: {\"field1\": \"value1\", \"field2\": \"value2\"}\n"
    "Пример НЕПРАВИЛЬНОГО ответа: {\"properties\": {\"field1\": \"value1\"}}\n"
    "Не повторяй одни и те же слова/фразы в значениях полей.\n"
)

# Regex to strip ```json ... ``` markdown fences from model output.
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)```\s*$", re.DOTALL)


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


def _extract_json(text: str) -> str:
    """Extract JSON from possibly markdown-fenced response."""
    text = text.strip()
    m = _JSON_FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    return text


def _uses_prompt_schema(model: str) -> bool:
    """Check if model requires prompt-based schema strategy."""
    return any(model.startswith(prefix) for prefix in _PROMPT_SCHEMA_MODEL_PREFIXES)


def _has_mandatory_reasoning(model: str) -> bool:
    """Check if model has mandatory reasoning that cannot be disabled."""
    return any(model.startswith(prefix) for prefix in _MANDATORY_REASONING_PREFIXES)


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

    def _build_extra_body(self, model: str, require_params: bool = True) -> dict[str, Any]:
        """Build extra_body with provider routing and optional reasoning control."""
        extra: dict[str, Any] = {
            "provider": {
                "sort": "price",
                "require_parameters": require_params,
            },
        }
        # Only disable reasoning for models that support it.
        # Models with mandatory reasoning (e.g. MiniMax M2.7) reject this parameter.
        if not _has_mandatory_reasoning(model):
            extra["reasoning"] = {"effort": "none"}
        return extra

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

        if _uses_prompt_schema(model):
            return await self._classify_prompt_schema(
                client=client,
                model=model,
                level=level,
                system_prompt=system_prompt,
                user_message=user_message,
                output_model=output_model,
                schema=schema,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        return await self._classify_strict_schema(
            client=client,
            model=model,
            level=level,
            system_prompt=system_prompt,
            user_message=user_message,
            output_model=output_model,
            schema=schema,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def _classify_strict_schema(
        self,
        *,
        client: Any,
        model: str,
        level: int,
        system_prompt: str,
        user_message: str,
        output_model: type[T],
        schema: dict[str, Any],
        max_tokens: int,
        temperature: float | None,
    ) -> T:
        """Strategy 1: json_schema strict — grammar-constrained decoding."""
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

        kwargs["extra_body"] = self._build_extra_body(model)

        response = await client.chat.completions.create(**kwargs)

        content = response.choices[0].message.content
        if not content:
            raise RuntimeError(
                f"OpenRouter returned empty response for level {level}, "
                f"model={model}, finish_reason={response.choices[0].finish_reason}"
            )

        data = json.loads(content)
        return output_model.model_validate(data)

    async def _classify_prompt_schema(
        self,
        *,
        client: Any,
        model: str,
        level: int,
        system_prompt: str,
        user_message: str,
        output_model: type[T],
        schema: dict[str, Any],
        max_tokens: int,
        temperature: float | None,
    ) -> T:
        """Strategy 2: json_object + schema in prompt.

        For models that don't support json_schema strict (e.g. MiniMax M2.7).
        Retries with temperature bump on truncation (repetition loops).
        """
        schema_str = json.dumps(schema, indent=2, ensure_ascii=False)
        augmented_system = (
            f"{system_prompt}\n\n"
            f"{_PROMPT_SCHEMA_INSTRUCTION}\n"
            f"JSON Schema:\n{schema_str}"
        )

        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            # Bump temperature on retry to break deterministic repetition loops
            temp = temperature if attempt == 1 else _RETRY_TEMPERATURE

            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": augmented_system},
                    {"role": "user", "content": user_message},
                ],
                "response_format": {"type": "json_object"},
            }
            if temp is not None:
                kwargs["temperature"] = temp

            kwargs["extra_body"] = self._build_extra_body(model)

            response = await client.chat.completions.create(**kwargs)
            finish_reason = response.choices[0].finish_reason
            content = response.choices[0].message.content

            if not content:
                last_error = RuntimeError(
                    f"OpenRouter returned empty response for level {level}, "
                    f"model={model}, finish_reason={finish_reason}"
                )
                logger.warning(
                    "Empty response from %s (level %d, attempt %d/%d)",
                    model, level, attempt, _MAX_RETRIES,
                )
                continue

            if finish_reason == "length":
                last_error = RuntimeError(
                    f"Response truncated (finish_reason=length, {len(content)} chars) "
                    f"for level {level}, model={model} — likely repetition loop"
                )
                logger.warning(
                    "Truncated response from %s (level %d, attempt %d/%d, %d chars)",
                    model, level, attempt, _MAX_RETRIES, len(content),
                )
                continue

            # Strip markdown fences if present (some models wrap JSON in ```json ... ```)
            content = _extract_json(content)

            try:
                data = json.loads(content)
            except json.JSONDecodeError as exc:
                last_error = exc
                logger.warning(
                    "Invalid JSON from %s (level %d, attempt %d/%d): %s",
                    model, level, attempt, _MAX_RETRIES, exc,
                )
                continue

            # Handle model wrapping response in {"properties": {...}}
            if "properties" in data and len(data) <= 2:
                inner = data.get("properties", {})
                if isinstance(inner, dict) and inner:
                    data = inner

            return output_model.model_validate(data)

        raise last_error or RuntimeError(
            f"All {_MAX_RETRIES} attempts failed for level {level}, model={model}"
        )
