"""Test StepFun Step 3.5 Flash structured output strategies on OpenRouter.

Tests three approaches:
1. json_schema strict + require_parameters — grammar-constrained decoding
2. json_schema strict (no require_parameters) — schema as hint
3. json_object + schema in prompt — fallback approach

Also tests reasoning control: whether reasoning: {effort: "none"} is accepted.

Step 3.5 Flash: 196B MoE (11B active), reasoning model, $0.10/$0.30 per MTok.
Provider: SiliconFlow (fp8).

Usage:
    python tests/test_step35_flash_structured.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from feap.schemas.product_card import ProductCard
from feap.schemas.section import SectionShortlistResult, SectionResult
from feap.schemas.group import GroupResult
from feap.schemas.position import ShortlistResult, PositionResult
from feap.schemas.subposition import SubpositionResult
from feap.schemas.subsubposition import SubsubpositionResult

load_dotenv()

MODEL = "stepfun/step-3.5-flash"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            ref_name = node["$ref"].split("/")[-1]
            return _inline_refs(dict(defs[ref_name]), defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


def _strip_defaults(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_defaults(v) for k, v in node.items() if k != "default"}
    if isinstance(node, list):
        return [_strip_defaults(item) for item in node]
    return node


def _prepare_schema(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.get("$defs", {})
    if defs:
        schema = _inline_refs(schema, defs)
    schema = dict(schema)
    schema.pop("title", None)
    schema = _strip_defaults(schema)
    return schema


def _extract_json(text: str) -> str:
    """Extract JSON from possibly markdown-fenced or mixed-content response."""
    text = text.strip()
    m = _JSON_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    if text.startswith("{"):
        return text
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        return text[first_brace:last_brace + 1]
    return text


PRODUCT = "Электрическая зубная щётка Oral-B Pro 3000 с аккумулятором Li-Ion, 3.7V"

QUICK_TEST = (
    ProductCard,
    "Ты эксперт по таможенной классификации. Обогати описание товара в структурированную карточку.",
    f"Товар: {PRODUCT}",
)

ALL_TEST_CASES: list[tuple[type[BaseModel], str, str]] = [
    QUICK_TEST,
    (
        SectionShortlistResult,
        "Ты эксперт по ТН ВЭД ЕАЭС. Определи 2-3 наиболее вероятных раздела для товара.",
        "Карточка товара: электрическая зубная щётка, бытовое назначение, встроенный электродвигатель, аккумулятор Li-Ion.\n"
        "Разделы ТН ВЭД:\n"
        "XVI - Машины, оборудование и механизмы; электротехническое оборудование\n"
        "XVIII - Инструменты и аппараты оптические, фотографические...\n"
        "XX - Разные промышленные товары\n"
        "VI - Продукция химической и связанных с ней отраслей промышленности",
    ),
    (
        SectionResult,
        "Ты эксперт по ТН ВЭД ЕАЭС. Выбери один раздел, проверив примечания.",
        "Карточка товара: электрическая зубная щётка с электродвигателем.\n"
        "Кандидаты: XVI, XX\n"
        "Примечание к разделу XVI: включает электромеханические бытовые приборы группы 85.\n"
        "Примечание к разделу XX: не включает товары раздела XVI.",
    ),
    (
        GroupResult,
        "Ты эксперт по ТН ВЭД ЕАЭС. Определи группу внутри раздела XVI.",
        "Раздел: XVI. Товар: электрическая зубная щётка.\n"
        "Группы раздела XVI:\n"
        "84 - Реакторы ядерные, котлы, оборудование и механические устройства\n"
        "85 - Электрические машины и оборудование, их части\n"
        "Примечание к гр. 85: включает электромеханические бытовые машины товарной позиции 8509.",
    ),
    (
        ShortlistResult,
        "Ты эксперт по ТН ВЭД ЕАЭС. Отбери 2-5 позиций-кандидатов в группе 85.",
        "Группа: 85. Товар: электрическая зубная щётка бытовая.\n"
        "Позиции:\n"
        "8509 - Машины электромеханические бытовые со встроенным электродвигателем\n"
        "8510 - Электробритвы, машинки для стрижки волос\n"
        "8516 - Электрические водонагреватели, приборы для обогрева...",
    ),
    (
        PositionResult,
        "Ты эксперт по ТН ВЭД ЕАЭС. Определи позицию, применяя ОПИ 1-5.",
        "Раздел: XVI, Группа: 85. Товар: электрическая зубная щётка бытовая.\n"
        "Кандидаты: 8509, 8510\n"
        "8509 — Машины электромеханические бытовые со встроенным электродвигателем\n"
        "8510 — Электробритвы, машинки для стрижки и аналогичные электроприборы\n"
        "Пояснение к 8509: включает зубные щётки электрические.",
    ),
    (
        SubpositionResult,
        "Ты эксперт по ТН ВЭД ЕАЭС. Определи субпозицию (6 цифр) внутри позиции 8509.",
        "Позиция: 8509. Товар: электрическая зубная щётка.\n"
        "Субпозиции:\n"
        "850940 - Измельчители пищевых продуктов и миксеры для продуктов\n"
        "850980 - Прочие машины\n"
        "ОПИ 6: субпозиции одного уровня сравниваются между собой.",
    ),
    (
        SubsubpositionResult,
        "Ты эксперт по ТН ВЭД ЕАЭС. Определи 10-значный код внутри субпозиции 850980.",
        "Субпозиция: 850980. Товар: электрическая зубная щётка.\n"
        "Коды:\n"
        "8509800001 - Щётки зубные электрические\n"
        "8509800009 - Прочие",
    ),
]


async def _call(
    client: AsyncOpenAI,
    messages: list,
    response_format: dict,
    extra_body: dict,
    max_tokens: int = 4096,
) -> tuple[str, str]:
    """Make an API call and return (content, finish_reason)."""
    response = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        response_format=response_format,
        extra_body=extra_body,
        temperature=0.0,
        max_tokens=max_tokens,
    )
    choice = response.choices[0]
    content = choice.message.content or ""

    if os.getenv("DEBUG"):
        print(f"    [DEBUG] finish_reason={choice.finish_reason}")
        print(f"    [DEBUG] content length={len(content)}")
        print(f"    [DEBUG] content[:300]={content[:300]!r}")
        reasoning = getattr(choice.message, "reasoning", None) or getattr(
            choice.message, "reasoning_content", None
        )
        if reasoning:
            rlen = len(reasoning) if isinstance(reasoning, str) else len(str(reasoning))
            print(f"    [DEBUG] reasoning length={rlen}")

    return content, choice.finish_reason


async def test_json_schema_strict(
    client: AsyncOpenAI,
    schema_class: type[BaseModel],
    task: str,
    user_msg: str,
) -> tuple[bool, float, str]:
    """Strategy 1: json_schema strict + require_parameters=True."""
    schema = _prepare_schema(schema_class.model_json_schema())
    t0 = time.monotonic()
    try:
        content, fr = await _call(
            client,
            [{"role": "system", "content": task}, {"role": "user", "content": user_msg}],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_class.__name__,
                    "strict": True,
                    "schema": schema,
                },
            },
            {
                "provider": {"sort": "price", "require_parameters": True},
                "reasoning": {"effort": "none"},
            },
        )
        latency = time.monotonic() - t0
        if not content:
            return False, latency, "Empty response"
        data = json.loads(_extract_json(content))
        result = schema_class.model_validate(data)
        return True, latency, _summarize(result)
    except Exception as e:
        return False, time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:200]}"


async def test_json_schema_no_require(
    client: AsyncOpenAI,
    schema_class: type[BaseModel],
    task: str,
    user_msg: str,
) -> tuple[bool, float, str]:
    """Strategy 2: json_schema strict WITHOUT require_parameters."""
    schema = _prepare_schema(schema_class.model_json_schema())
    t0 = time.monotonic()
    try:
        content, fr = await _call(
            client,
            [{"role": "system", "content": task}, {"role": "user", "content": user_msg}],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_class.__name__,
                    "strict": True,
                    "schema": schema,
                },
            },
            {
                "provider": {"sort": "price"},
                "reasoning": {"effort": "none"},
            },
        )
        latency = time.monotonic() - t0
        if not content:
            return False, latency, "Empty response"
        data = json.loads(_extract_json(content))
        result = schema_class.model_validate(data)
        return True, latency, _summarize(result)
    except Exception as e:
        return False, time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:200]}"


async def test_json_schema_no_reasoning(
    client: AsyncOpenAI,
    schema_class: type[BaseModel],
    task: str,
    user_msg: str,
) -> tuple[bool, float, str]:
    """Strategy 2b: json_schema strict WITHOUT reasoning control (test if mandatory)."""
    schema = _prepare_schema(schema_class.model_json_schema())
    t0 = time.monotonic()
    try:
        content, fr = await _call(
            client,
            [{"role": "system", "content": task}, {"role": "user", "content": user_msg}],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_class.__name__,
                    "strict": True,
                    "schema": schema,
                },
            },
            {
                "provider": {"sort": "price"},
                # No reasoning parameter — test if model works without it
            },
        )
        latency = time.monotonic() - t0
        if not content:
            return False, latency, "Empty response"
        data = json.loads(_extract_json(content))
        result = schema_class.model_validate(data)
        return True, latency, _summarize(result)
    except Exception as e:
        return False, time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:200]}"


async def test_json_object_prompt(
    client: AsyncOpenAI,
    schema_class: type[BaseModel],
    task: str,
    user_msg: str,
) -> tuple[bool, float, str]:
    """Strategy 3: json_object + schema in prompt."""
    schema = _prepare_schema(schema_class.model_json_schema())
    schema_str = json.dumps(schema, indent=2, ensure_ascii=False)

    system_prompt = (
        f"{task}\n\n"
        "Ответь СТРОГО в JSON по следующей JSON Schema.\n"
        "Используй ТОЛЬКО поля из schema, никаких дополнительных.\n"
        'ВАЖНО: Верни ПЛОСКИЙ объект с полями из \'properties\', '
        'НЕ оборачивай в {"properties": {...}}.\n'
        "Все обязательные поля (required) ДОЛЖНЫ присутствовать.\n"
        "Не повторяй одни и те же слова/фразы в значениях полей.\n\n"
        f"JSON Schema:\n{schema_str}"
    )

    t0 = time.monotonic()
    try:
        content, fr = await _call(
            client,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            {"type": "json_object"},
            {
                "provider": {"sort": "price", "require_parameters": True},
                "reasoning": {"effort": "none"},
            },
        )
        latency = time.monotonic() - t0
        if not content:
            return False, latency, f"Empty (finish_reason={fr})"
        if fr == "length":
            return False, latency, f"Truncated ({len(content)} chars)"
        content = _extract_json(content)
        data = json.loads(content)
        if isinstance(data, dict) and "properties" in data and len(data) <= 2:
            inner = data.get("properties", {})
            if isinstance(inner, dict) and inner:
                data = inner
        result = schema_class.model_validate(data)
        return True, latency, _summarize(result)
    except Exception as e:
        return False, time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:200]}"


async def test_json_object_prompt_no_reasoning(
    client: AsyncOpenAI,
    schema_class: type[BaseModel],
    task: str,
    user_msg: str,
) -> tuple[bool, float, str]:
    """Strategy 3b: json_object + schema in prompt WITHOUT reasoning control."""
    schema = _prepare_schema(schema_class.model_json_schema())
    schema_str = json.dumps(schema, indent=2, ensure_ascii=False)

    system_prompt = (
        f"{task}\n\n"
        "Ответь СТРОГО в JSON по следующей JSON Schema.\n"
        "Используй ТОЛЬКО поля из schema, никаких дополнительных.\n"
        'ВАЖНО: Верни ПЛОСКИЙ объект с полями из \'properties\', '
        'НЕ оборачивай в {"properties": {...}}.\n'
        "Все обязательные поля (required) ДОЛЖНЫ присутствовать.\n"
        "Не повторяй одни и те же слова/фразы в значениях полей.\n\n"
        f"JSON Schema:\n{schema_str}"
    )

    t0 = time.monotonic()
    try:
        content, fr = await _call(
            client,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            {"type": "json_object"},
            {
                "provider": {"sort": "price", "require_parameters": True},
                # No reasoning parameter
            },
        )
        latency = time.monotonic() - t0
        if not content:
            return False, latency, f"Empty (finish_reason={fr})"
        if fr == "length":
            return False, latency, f"Truncated ({len(content)} chars)"
        content = _extract_json(content)
        data = json.loads(content)
        if isinstance(data, dict) and "properties" in data and len(data) <= 2:
            inner = data.get("properties", {})
            if isinstance(inner, dict) and inner:
                data = inner
        result = schema_class.model_validate(data)
        return True, latency, _summarize(result)
    except Exception as e:
        return False, time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:200]}"


async def test_json_object_no_require(
    client: AsyncOpenAI,
    schema_class: type[BaseModel],
    task: str,
    user_msg: str,
) -> tuple[bool, float, str]:
    """Strategy 4: json_object + schema in prompt, no require_parameters."""
    schema = _prepare_schema(schema_class.model_json_schema())
    schema_str = json.dumps(schema, indent=2, ensure_ascii=False)

    system_prompt = (
        f"{task}\n\n"
        "Ответь СТРОГО в JSON по следующей JSON Schema.\n"
        "Используй ТОЛЬКО поля из schema, никаких дополнительных.\n"
        'ВАЖНО: Верни ПЛОСКИЙ объект с полями из \'properties\', '
        'НЕ оборачивай в {"properties": {...}}.\n'
        "Все обязательные поля (required) ДОЛЖНЫ присутствовать.\n"
        "Не повторяй одни и те же слова/фразы в значениях полей.\n\n"
        f"JSON Schema:\n{schema_str}"
    )

    t0 = time.monotonic()
    try:
        content, fr = await _call(
            client,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            {"type": "json_object"},
            {"provider": {"sort": "price"}},
        )
        latency = time.monotonic() - t0
        if not content:
            return False, latency, f"Empty (finish_reason={fr})"
        if fr == "length":
            return False, latency, f"Truncated ({len(content)} chars)"
        content = _extract_json(content)
        data = json.loads(content)
        if isinstance(data, dict) and "properties" in data and len(data) <= 2:
            inner = data.get("properties", {})
            if isinstance(inner, dict) and inner:
                data = inner
        result = schema_class.model_validate(data)
        return True, latency, _summarize(result)
    except Exception as e:
        return False, time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:200]}"


async def test_plain_text_prompt(
    client: AsyncOpenAI,
    schema_class: type[BaseModel],
    task: str,
    user_msg: str,
) -> tuple[bool, float, str]:
    """Strategy 5: NO response_format, schema in prompt, parse JSON from text."""
    schema = _prepare_schema(schema_class.model_json_schema())
    schema_str = json.dumps(schema, indent=2, ensure_ascii=False)

    system_prompt = (
        f"{task}\n\n"
        "Ответь СТРОГО в JSON по следующей JSON Schema.\n"
        "Используй ТОЛЬКО поля из schema, никаких дополнительных.\n"
        'ВАЖНО: Верни ПЛОСКИЙ объект с полями из \'properties\', '
        'НЕ оборачивай в {"properties": {...}}.\n'
        "Все обязательные поля (required) ДОЛЖНЫ присутствовать.\n"
        "Не повторяй одни и те же слова/фразы в значениях полей.\n"
        "Верни ТОЛЬКО JSON, без пояснений и без markdown.\n\n"
        f"JSON Schema:\n{schema_str}"
    )

    t0 = time.monotonic()
    try:
        # Step 3.5 Flash has mandatory reasoning that counts toward completion tokens.
        # Need high max_tokens (16384+) to avoid truncation from reasoning overhead.
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            # No response_format at all — SiliconFlow rejects json_object/json_schema
            extra_body={"provider": {"sort": "price"}},
            temperature=0.0,
            max_tokens=16384,
        )
        latency = time.monotonic() - t0
        choice = response.choices[0]
        content = choice.message.content or ""

        if os.getenv("DEBUG"):
            print(f"    [DEBUG] finish_reason={choice.finish_reason}")
            print(f"    [DEBUG] content length={len(content)}")
            print(f"    [DEBUG] content[:500]={content[:500]!r}")

        if not content:
            return False, latency, f"Empty (finish_reason={choice.finish_reason})"

        extracted = _extract_json(content)
        data = json.loads(extracted)
        if isinstance(data, dict) and "properties" in data and len(data) <= 2:
            inner = data.get("properties", {})
            if isinstance(inner, dict) and inner:
                data = inner
        result = schema_class.model_validate(data)
        return True, latency, _summarize(result)
    except json.JSONDecodeError as e:
        return False, time.monotonic() - t0, f"JSONDecodeError: {e} | raw[:200]={content[:200]!r}"
    except Exception as e:
        return False, time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:200]}"


def _summarize(obj: BaseModel) -> str:
    d = obj.model_dump(exclude_defaults=True)
    keys = list(d.keys())[:4]
    parts = []
    for k in keys:
        v = d[k]
        if isinstance(v, str) and len(v) > 60:
            v = v[:60] + "..."
        elif isinstance(v, list) and len(v) > 2:
            v = f"[{len(v)} items]"
        parts.append(f"{k}={v!r}")
    return ", ".join(parts)


STRATEGIES = [
    ("1: json_schema strict + require_params", test_json_schema_strict),
    ("2a: json_schema strict (no require_params)", test_json_schema_no_require),
    ("2b: json_schema strict (no reasoning ctrl)", test_json_schema_no_reasoning),
    ("3a: json_object + prompt + reasoning:none", test_json_object_prompt),
    ("3b: json_object + prompt (no reasoning ctrl)", test_json_object_prompt_no_reasoning),
    ("4: json_object + prompt (no require_params)", test_json_object_no_require),
    ("5: plain text + prompt (no response_format)", test_plain_text_prompt),
]


async def main() -> None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set")
        sys.exit(1)

    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
    schema_class, task, user_msg = QUICK_TEST

    print(f"Model: {MODEL}")
    print(f"Quick test schema: {schema_class.__name__}")
    print("=" * 80)

    # --- Phase 1: Quick test all strategies with ProductCard ---
    print("\nPHASE 1: Strategy discovery (ProductCard only)")
    print("-" * 80)

    best_name = None
    best_fn = None

    for name, fn in STRATEGIES:
        print(f"\n  Strategy {name}:")
        ok, lat, det = await fn(client, schema_class, task, user_msg)
        status = "PASS" if ok else "FAIL"
        print(f"    [{status}]  {lat:5.1f}s  {det[:120]}")
        if ok and best_fn is None:
            best_name = name
            best_fn = fn

    if best_fn is None:
        print("\n" + "=" * 80)
        print("ALL STRATEGIES FAILED — model not usable for structured output")
        sys.exit(1)

    # --- Phase 2: Full suite with best strategy ---
    print("\n" + "=" * 80)
    print(f"BEST STRATEGY: {best_name}")
    print("Running full suite (8 schemas)...")
    print("-" * 80)

    results = []
    for sc, t, um in ALL_TEST_CASES:
        r_ok, r_lat, r_det = await best_fn(client, sc, t, um)
        status = "PASS" if r_ok else "FAIL"
        print(f"  [{status}] {sc.__name__:30s}  {r_lat:5.1f}s  {r_det[:100]}")
        results.append((sc.__name__, r_ok, r_lat))

    print("=" * 80)
    passed = sum(1 for _, r, _ in results if r)
    total = len(results)
    avg = sum(lat for _, _, lat in results) / total if total else 0
    print(f"Result: {passed}/{total} passed, avg latency {avg:.1f}s")
    print(f"Best strategy: {best_name}")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
