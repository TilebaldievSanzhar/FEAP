"""OpenAI reasoning-family compatibility: no `temperature`, strict JSON Schema.

gpt-5.x / o-series endpoints on OpenRouter reject `temperature` (404 with
require_parameters) and demand `additionalProperties: false` + full `required`
on every object (400 invalid_json_schema). These tests pin both adaptations and
guard that other providers keep the unmodified schema.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from feap.dt.extractors.schemas import EXTRACTION_MODELS, DocTypeResult
from feap.llm.openrouter_provider import (
    OpenRouterProvider,
    _needs_openai_strict_schema,
    _prepare_schema,
    _strictify_schema,
    _supports_temperature,
)
from feap.ocr.schemas import StructuredProducts
from feap.schemas.group import GroupResult
from feap.schemas.position import PositionResult, ShortlistResult
from feap.schemas.product_card import ProductCard
from feap.schemas.section import SectionResult, SectionShortlistResult
from feap.schemas.subposition import SubpositionResult
from feap.schemas.subsubposition import (
    SubsubpositionResult,
    SubsubpositionShortlistResult,
)

LLM_OUTPUT_MODELS = [
    ProductCard, SectionShortlistResult, SectionResult, GroupResult,
    ShortlistResult, PositionResult, SubpositionResult,
    SubsubpositionShortlistResult, SubsubpositionResult,
    DocTypeResult, StructuredProducts, *EXTRACTION_MODELS.values(),
]


def _objects(node: Any, path: str = "$"):
    """Yield (path, object_schema) for every object node in a JSON Schema."""
    if isinstance(node, list):
        for i, item in enumerate(node):
            yield from _objects(item, f"{path}[{i}]")
        return
    if not isinstance(node, dict):
        return
    if node.get("type") == "object" or "properties" in node:
        yield path, node
    for key, value in node.items():
        if key == "properties":
            for pk, pv in value.items():
                yield from _objects(pv, f"{path}.{pk}")
        elif key in ("items", "anyOf"):
            yield from _objects(value, f"{path}[{key}]")


class TestTemperatureSupport:
    @pytest.mark.parametrize("model", [
        "openai/gpt-5.6-luna", "openai/gpt-5.5", "openai/o3", "openai/o4-mini",
    ])
    def test_reasoning_models_reject_temperature(self, model: str):
        assert _supports_temperature(model) is False

    @pytest.mark.parametrize("model", [
        "google/gemini-3.1-flash-lite", "anthropic/claude-sonnet-4-6",
        "deepseek/deepseek-v3.2", "minimax/minimax-m2.7",
    ])
    def test_other_models_keep_temperature(self, model: str):
        assert _supports_temperature(model) is True


class TestStrictifySchema:
    @pytest.mark.parametrize("output_model", LLM_OUTPUT_MODELS,
                             ids=lambda m: m.__name__)
    def test_every_object_is_openai_strict(self, output_model):
        schema = _strictify_schema(_prepare_schema(output_model.model_json_schema()))
        for path, obj in _objects(schema):
            props = obj.get("properties")
            assert props is not None, f"{output_model.__name__}: free-form object at {path}"
            assert obj.get("additionalProperties") is False, (
                f"{output_model.__name__}: missing additionalProperties:false at {path}"
            )
            assert set(obj["required"]) == set(props), (
                f"{output_model.__name__}: `required` must list all properties at {path}"
            )

    def test_only_openai_models_are_normalized(self):
        assert _needs_openai_strict_schema("openai/gpt-5.6-luna") is True
        assert _needs_openai_strict_schema("google/gemini-3.1-flash-lite") is False
        assert _needs_openai_strict_schema("anthropic/claude-sonnet-4-6") is False


class _CapturingProvider(OpenRouterProvider):
    """Captures request kwargs instead of calling OpenRouter."""

    def __init__(self, payload: dict[str, Any]):
        super().__init__()
        self.kwargs: dict[str, Any] = {}
        self._payload = payload

    def _get_client(self) -> Any:
        return object()

    async def _call_api(self, client: Any, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        message = type("_Msg", (), {"content": json.dumps(self._payload)})()
        choice = type("_Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("_Resp", (), {"choices": [choice]})()


_MINIMAL_SHORTLIST = {
    "group": "85", "physical_nature_analysis": "", "key_differentiators": "",
    "shortlisted": ["8509"], "reasoning": "test",
}


class TestRequestShape:
    async def test_openai_model_omits_temperature_and_strictifies(self):
        provider = _CapturingProvider(_MINIMAL_SHORTLIST)
        await provider.classify(
            level=3, system_prompt="sys", user_message="usr",
            output_model=ShortlistResult, model_override="openai/gpt-5.6-luna",
        )
        assert "temperature" not in provider.kwargs
        schema = provider.kwargs["response_format"]["json_schema"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])

    async def test_non_openai_model_keeps_temperature_and_plain_schema(self):
        provider = _CapturingProvider(_MINIMAL_SHORTLIST)
        await provider.classify(
            level=3, system_prompt="sys", user_message="usr",
            output_model=ShortlistResult, model_override="anthropic/claude-sonnet-4-6",
        )
        assert provider.kwargs["temperature"] == 0.0
        schema = provider.kwargs["response_format"]["json_schema"]["schema"]
        assert "additionalProperties" not in schema
        assert set(schema["required"]) != set(schema["properties"])
