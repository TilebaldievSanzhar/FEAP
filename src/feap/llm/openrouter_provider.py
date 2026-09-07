"""OpenRouter provider — structured output via JSON Schema (OpenAI-compatible API).

Supports three structured output strategies:
1. json_schema strict — grammar-constrained decoding (Anthropic, OpenAI, DeepSeek, etc.)
2. json_object + schema in prompt — for models without strict support (MiniMax, etc.)
3. plain text + schema in prompt — for models with no response_format support (StepFun, etc.)

See docs/structured_output_providers.md for details.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from feap.config import settings
from feap.llm.models import get_max_tokens, get_model, get_temperature
from feap.llm.provider import LLMProvider
from feap.llm.rate_limit import RateLimiter, build_rate_limiter

T = TypeVar("T", bound=BaseModel)

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

logger = logging.getLogger(__name__)

# Models that require json_object + schema-in-prompt strategy.
# These models don't support json_schema strict via OpenRouter providers.
# Pattern match: if the resolved model ID *starts with* any of these prefixes,
# the prompt-based strategy is used.
_PROMPT_SCHEMA_MODEL_PREFIXES: tuple[str, ...] = (
    "minimax/minimax-m2",
    "google/gemini-3.1-flash-lite",
)

# Models that require plain text + schema-in-prompt strategy.
# These models don't support ANY response_format (neither json_schema nor json_object).
# Uses no response_format at all — schema in prompt + JSON extraction from text.
_PLAIN_TEXT_SCHEMA_MODEL_PREFIXES: tuple[str, ...] = (
    "stepfun/step-",
)

# Models whose OpenRouter endpoints reject `temperature` (OpenAI reasoning
# family: gpt-5.x, o1/o3/o4). With provider.require_parameters=True sending it
# yields 404 "No endpoints found that can handle the requested parameters".
_NO_TEMPERATURE_MODEL_PREFIXES: tuple[str, ...] = (
    "openai/gpt-5",
    "openai/o1",
    "openai/o3",
    "openai/o4",
)

# Models whose json_schema strict mode follows OpenAI's stricter dialect:
# every object needs additionalProperties:false and `required` listing ALL
# properties. Pydantic emits neither, so the schema is normalized first.
_OPENAI_STRICT_SCHEMA_PREFIXES: tuple[str, ...] = (
    "openai/",
)

# Models with mandatory reasoning (thinking) that cannot be disabled.
# For these models, we must NOT send reasoning: {effort: "none"} — the
# endpoint answers 400 "Reasoning is mandatory for this endpoint and cannot
# be disabled". Gemini 3.5 Flash Lite still reports reasoning_tokens = 0 in
# practice, so no extra token budget is needed for it.
_MANDATORY_REASONING_PREFIXES: tuple[str, ...] = (
    "minimax/minimax-m2",
    "stepfun/step-",
    "google/gemini-3.5-flash-lite",
)

# Models that need higher max_tokens due to mandatory reasoning overhead.
# Reasoning tokens count toward completion_tokens budget.
_HIGH_MAX_TOKENS_PREFIXES: tuple[str, ...] = (
    "stepfun/step-",
)

# Minimum max_tokens for models with mandatory reasoning overhead.
_HIGH_MAX_TOKENS_MIN = 16384

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
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


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


def _strictify_schema(node: Any) -> Any:
    """Normalize a JSON Schema to OpenAI's strict Structured Outputs dialect.

    OpenAI rejects a schema unless every object carries
    `additionalProperties: false` and a `required` array naming *all* of its
    properties. Optionality is expressed by nullable types instead, which
    Pydantic already emits for `X | None` fields; fields with defaults simply
    become mandatory in the response (the model has to emit the default-ish
    value explicitly).
    """
    if isinstance(node, list):
        return [_strictify_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    out = {k: _strictify_schema(v) for k, v in node.items()}
    props = out.get("properties")
    if isinstance(props, dict):
        out["additionalProperties"] = False
        out["required"] = list(props.keys())
    return out


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
    """Extract JSON from possibly markdown-fenced or mixed-content response.

    Handles cases where model returns markdown text with embedded JSON fences,
    or plain text with a JSON object somewhere inside.
    """
    text = text.strip()
    # 1. Try to find ```json ... ``` fence anywhere in text
    m = _JSON_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    # 2. If text starts with '{', assume it's JSON
    if text.startswith("{"):
        return text
    # 3. Fallback: find first '{' and last '}' to extract embedded JSON object
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        return text[first_brace:last_brace + 1]
    return text


def _uses_prompt_schema(model: str) -> bool:
    """Check if model requires json_object + prompt-based schema strategy."""
    return any(model.startswith(prefix) for prefix in _PROMPT_SCHEMA_MODEL_PREFIXES)


def _uses_plain_text_schema(model: str) -> bool:
    """Check if model requires plain text + prompt-based schema strategy.

    These models don't support any response_format parameter.
    """
    return any(model.startswith(prefix) for prefix in _PLAIN_TEXT_SCHEMA_MODEL_PREFIXES)


def _supports_temperature(model: str) -> bool:
    """Check if the model's endpoints accept the `temperature` parameter."""
    return not any(model.startswith(p) for p in _NO_TEMPERATURE_MODEL_PREFIXES)


def _needs_openai_strict_schema(model: str) -> bool:
    """Check if the model requires OpenAI's strict JSON Schema dialect."""
    return any(model.startswith(p) for p in _OPENAI_STRICT_SCHEMA_PREFIXES)


def _has_mandatory_reasoning(model: str) -> bool:
    """Check if model has mandatory reasoning that cannot be disabled."""
    return any(model.startswith(prefix) for prefix in _MANDATORY_REASONING_PREFIXES)


def _needs_high_max_tokens(model: str) -> bool:
    """Check if model needs higher max_tokens due to reasoning overhead."""
    return any(model.startswith(prefix) for prefix in _HIGH_MAX_TOKENS_PREFIXES)


class OpenRouterProvider(LLMProvider):
    """OpenRouter API — routes to any model via OpenAI-compatible interface.

    Concurrency control:
    - Per-process semaphore caps simultaneous in-flight calls
      (settings.LLM_MAX_CONCURRENT). Applied to every call regardless of
      caller (sync /classify endpoint or arq worker).
    - Optional rate limiter (RPM cap, local or Redis-backed) layered on top
      via settings.LLM_RATE_LIMIT_PER_MIN / LLM_RATE_LIMITER_BACKEND.

    Wrapping the limiters inside the provider — rather than at the worker
    layer — guarantees that *all* paths into OpenRouter respect the same
    budget, including the synchronous single-product endpoint.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._semaphore: asyncio.Semaphore | None = None
        self._rate_limiter: RateLimiter | None = None
        self._redis_client: Any = None  # set by configure_distributed_limit() if needed

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                base_url=_OPENROUTER_BASE_URL,
                api_key=settings.OPENROUTER_API_KEY,
            )
        return self._client

    def _get_semaphore(self) -> asyncio.Semaphore:
        # Lazy-init so the semaphore binds to the running event loop,
        # not the one active at module import time.
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENT)
        return self._semaphore

    def _get_rate_limiter(self) -> RateLimiter:
        if self._rate_limiter is None:
            self._rate_limiter = build_rate_limiter(
                limit_per_min=settings.LLM_RATE_LIMIT_PER_MIN,
                backend=settings.LLM_RATE_LIMITER_BACKEND,
                redis=self._redis_client,
            )
        return self._rate_limiter

    def configure_distributed_limit(self, redis_client: Any) -> None:
        """Attach a Redis client so the rate limiter is shared across workers.

        Called from the arq worker startup hook when LLM_RATE_LIMITER_BACKEND='redis'.
        Resets any previously-built limiter so subsequent calls pick up the new client.
        """
        self._redis_client = redis_client
        self._rate_limiter = None

    async def _call_api(self, client: Any, **kwargs: Any) -> Any:
        """Single chokepoint for OpenRouter requests — applies semaphore + RPM cap."""
        sem = self._get_semaphore()
        limiter = self._get_rate_limiter()
        async with sem, limiter:
            return await client.chat.completions.create(**kwargs)

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

        # OpenAI's strict dialect demands additionalProperties:false + full
        # `required` on every object; other providers accept the plain schema.
        if _needs_openai_strict_schema(model):
            schema = _strictify_schema(schema)

        # Reasoning-family models (gpt-5.x, o-series) reject `temperature`;
        # sending it with require_parameters=True fails provider routing.
        if temperature is not None and not _supports_temperature(model):
            temperature = None

        # Bump max_tokens for models with mandatory reasoning overhead
        if _needs_high_max_tokens(model):
            max_tokens = max(max_tokens, _HIGH_MAX_TOKENS_MIN)

        if _uses_plain_text_schema(model):
            return await self._classify_plain_text_schema(
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

        response = await self._call_api(client, **kwargs)

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
            # Bump temperature on retry to break deterministic repetition loops.
            # Skipped when temperature is unset — either by config or because
            # the model rejects the parameter (see _supports_temperature).
            temp = temperature if attempt == 1 or temperature is None else _RETRY_TEMPERATURE

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

            response = await self._call_api(client, **kwargs)
            finish_reason = response.choices[0].finish_reason
            content = response.choices[0].message.content

            if not content:
                # Check reasoning field for models with mandatory thinking
                reasoning = getattr(response.choices[0].message, "reasoning", None)
                last_error = RuntimeError(
                    f"OpenRouter returned empty response for level {level}, "
                    f"model={model}, finish_reason={finish_reason}"
                )
                logger.warning(
                    "Empty response from %s (level %d, attempt %d/%d, "
                    "finish_reason=%s, has_reasoning=%s)",
                    model, level, attempt, _MAX_RETRIES,
                    finish_reason, bool(reasoning),
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
            raw_content = content
            content = _extract_json(content)

            try:
                data = json.loads(content)
            except json.JSONDecodeError as exc:
                last_error = exc
                logger.warning(
                    "Invalid JSON from %s (level %d, attempt %d/%d): %s | "
                    "raw_content_len=%d, extracted_len=%d, first_100=%r",
                    model, level, attempt, _MAX_RETRIES, exc,
                    len(raw_content), len(content), raw_content[:100],
                )
                continue

            # Handle model wrapping response in {"properties": {...}}
            if isinstance(data, dict) and "properties" in data and len(data) <= 2:
                inner = data.get("properties", {})
                if isinstance(inner, dict) and inner:
                    data = inner

            # Handle model returning a list instead of an object
            if isinstance(data, list):
                logger.warning(
                    "Model returned list instead of object for level %d, "
                    "attempting to use first element",
                    level,
                )
                if data and isinstance(data[0], dict):
                    data = data[0]
                else:
                    last_error = RuntimeError(
                        f"Model returned empty list for level {level}"
                    )
                    continue

            try:
                return output_model.model_validate(data)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Pydantic validation failed for %s (level %d, attempt %d/%d): %s",
                    model, level, attempt, _MAX_RETRIES, str(exc)[:200],
                )
                continue

        raise last_error or RuntimeError(
            f"All {_MAX_RETRIES} attempts failed for level {level}, model={model}"
        )

    async def _classify_plain_text_schema(
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
        """Strategy 3: plain text + schema in prompt — no response_format at all.

        For models where providers don't support any response_format parameter
        (e.g. StepFun Step 3.5 Flash on SiliconFlow).
        Retries with temperature bump on truncation or parse failure.
        """
        schema_str = json.dumps(schema, indent=2, ensure_ascii=False)
        augmented_system = (
            f"{system_prompt}\n\n"
            f"{_PROMPT_SCHEMA_INSTRUCTION}\n"
            "Верни ТОЛЬКО JSON, без пояснений и без markdown.\n\n"
            f"JSON Schema:\n{schema_str}"
        )

        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            temp = temperature if attempt == 1 or temperature is None else _RETRY_TEMPERATURE

            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": augmented_system},
                    {"role": "user", "content": user_message},
                ],
                # No response_format — provider doesn't support it
            }
            if temp is not None:
                kwargs["temperature"] = temp

            kwargs["extra_body"] = self._build_extra_body(model, require_params=False)

            response = await self._call_api(client, **kwargs)
            finish_reason = response.choices[0].finish_reason
            content = response.choices[0].message.content

            if not content:
                last_error = RuntimeError(
                    f"OpenRouter returned empty response for level {level}, "
                    f"model={model}, finish_reason={finish_reason}"
                )
                logger.warning(
                    "Empty response from %s (level %d, attempt %d/%d, "
                    "finish_reason=%s)",
                    model, level, attempt, _MAX_RETRIES, finish_reason,
                )
                continue

            if finish_reason == "length":
                last_error = RuntimeError(
                    f"Response truncated (finish_reason=length, {len(content)} chars) "
                    f"for level {level}, model={model} — likely reasoning overflow"
                )
                logger.warning(
                    "Truncated response from %s (level %d, attempt %d/%d, %d chars)",
                    model, level, attempt, _MAX_RETRIES, len(content),
                )
                continue

            # Extract JSON from potentially mixed text+JSON response
            raw_content = content
            content = _extract_json(content)

            try:
                data = json.loads(content)
            except json.JSONDecodeError as exc:
                last_error = exc
                logger.warning(
                    "Invalid JSON from %s (level %d, attempt %d/%d): %s | "
                    "raw_content_len=%d, extracted_len=%d, first_100=%r",
                    model, level, attempt, _MAX_RETRIES, exc,
                    len(raw_content), len(content), raw_content[:100],
                )
                continue

            # Handle model wrapping response in {"properties": {...}}
            if isinstance(data, dict) and "properties" in data and len(data) <= 2:
                inner = data.get("properties", {})
                if isinstance(inner, dict) and inner:
                    data = inner

            # Handle model returning a list instead of an object
            if isinstance(data, list):
                logger.warning(
                    "Model returned list instead of object for level %d, "
                    "attempting to use first element",
                    level,
                )
                if data and isinstance(data[0], dict):
                    data = data[0]
                else:
                    last_error = RuntimeError(
                        f"Model returned empty list for level {level}"
                    )
                    continue

            try:
                return output_model.model_validate(data)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Pydantic validation failed for %s (level %d, attempt %d/%d): %s",
                    model, level, attempt, _MAX_RETRIES, str(exc)[:200],
                )
                continue

        raise last_error or RuntimeError(
            f"All {_MAX_RETRIES} attempts failed for level {level}, model={model}"
        )
