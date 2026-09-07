"""Per-model request shaping in OpenRouterProvider.

Each model family needs a different combination of structured-output strategy,
reasoning control and temperature. These offline tests pin the routing table so
adding a model can't silently change what another one sends.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from feap.llm.openrouter_provider import (
    OpenRouterProvider,
    _has_mandatory_reasoning,
    _uses_plain_text_schema,
    _uses_prompt_schema,
)
from feap.schemas.position import ShortlistResult

_MINIMAL_SHORTLIST = {
    "group": "85", "physical_nature_analysis": "", "key_differentiators": "",
    "shortlisted": ["8509"], "reasoning": "test",
}


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


async def _capture(model: str) -> dict[str, Any]:
    provider = _CapturingProvider(_MINIMAL_SHORTLIST)
    await provider.classify(
        level=3, system_prompt="sys", user_message="usr",
        output_model=ShortlistResult, model_override=model,
    )
    return provider.kwargs


class TestMandatoryReasoning:
    """Endpoints that answer 400 to `reasoning: {effort: "none"}`."""

    @pytest.mark.parametrize("model", [
        "minimax/minimax-m2.7", "stepfun/step-3.5-flash",
        "google/gemini-3.5-flash-lite",
    ])
    def test_reasoning_is_not_disabled(self, model: str):
        assert _has_mandatory_reasoning(model) is True

    @pytest.mark.parametrize("model", [
        "google/gemini-3.1-flash-lite", "anthropic/claude-sonnet-4-6",
        "openai/gpt-5.6-luna", "deepseek/deepseek-v3.2",
    ])
    def test_reasoning_is_disabled_elsewhere(self, model: str):
        assert _has_mandatory_reasoning(model) is False

    async def test_gemini_35_omits_reasoning_from_request(self):
        kwargs = await _capture("google/gemini-3.5-flash-lite")
        assert "reasoning" not in kwargs["extra_body"]

    async def test_gemini_31_still_sends_reasoning_none(self):
        kwargs = await _capture("google/gemini-3.1-flash-lite")
        assert kwargs["extra_body"]["reasoning"] == {"effort": "none"}


class TestStructuredOutputStrategy:
    async def test_gemini_35_uses_strict_json_schema(self):
        """3.5 Flash Lite does grammar-constrained decoding; 3.1 did not."""
        model = "google/gemini-3.5-flash-lite"
        assert _uses_prompt_schema(model) is False
        assert _uses_plain_text_schema(model) is False

        kwargs = await _capture(model)
        rf = kwargs["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["strict"] is True
        # Gemini takes the plain Pydantic schema — no OpenAI strict dialect.
        assert "additionalProperties" not in rf["json_schema"]["schema"]
        assert kwargs["temperature"] == 0.0

    async def test_gemini_31_uses_json_object(self):
        kwargs = await _capture("google/gemini-3.1-flash-lite")
        assert kwargs["response_format"] == {"type": "json_object"}
