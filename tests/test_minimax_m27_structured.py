"""Test MiniMax M2.7 structured output strategies on OpenRouter.

Tests three approaches:
1. json_schema strict + require_parameters — grammar-constrained decoding
2. json_schema strict (no require_parameters) — schema as hint
3. json_object + schema in prompt — fallback approach

Note: MiniMax M2.7 has MANDATORY reasoning (thinking tokens).
The reasoning: {effort: "none"} parameter CANNOT be used.

Usage:
    python tests/test_minimax_m27_structured.py
"""

from __future__ import annotations

import asyncio
import json
import os
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

MODEL = "minimax/minimax-m2.7"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


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


def _extract_json(text: str) -> str:
    """Extract JSON from possibly markdown-fenced response."""
    text = text.strip()
    # Strip ```json ... ``` fences
    if text.startswith("```"):
        # Find the end of the first line (```json or ```)
        first_nl = text.index("\n")
        # Find closing ```
        last_fence = text.rfind("```")
        if last_fence > first_nl:
            text = text[first_nl + 1:last_fence].strip()
    return text


async def _call(client: AsyncOpenAI, messages: list, response_format: dict, extra_body: dict, max_tokens: int = 4096) -> Any:
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

    # Debug: show raw response structure
    if os.getenv("DEBUG"):
        print(f"    [DEBUG] finish_reason={choice.finish_reason}")
        print(f"    [DEBUG] content length={len(content)}")
        print(f"    [DEBUG] content[:300]={content[:300]!r}")
        # Check for reasoning field (OpenRouter may put thinking here)
        reasoning = getattr(choice.message, "reasoning", None) or getattr(choice.message, "reasoning_content", None)
        if reasoning:
            print(f"    [DEBUG] reasoning length={len(reasoning)}")

    return content, choice.finish_reason


async def test_json_schema_strict(client: AsyncOpenAI, schema_class: type[BaseModel], task: str, user_msg: str) -> tuple[bool, float, str]:
    """Strategy 1: json_schema strict with require_parameters."""
    schema = _prepare_schema(schema_class.model_json_schema())
    t0 = time.monotonic()
    try:
        content, fr = await _call(
            client,
            [{"role": "system", "content": task}, {"role": "user", "content": user_msg}],
            {"type": "json_schema", "json_schema": {"name": schema_class.__name__, "strict": True, "schema": schema}},
            {"provider": {"sort": "price", "require_parameters": True}},
        )
        latency = time.monotonic() - t0
        if not content:
            return False, latency, "Empty response"
        data = json.loads(_extract_json(content))
        schema_class.model_validate(data)
        return True, latency, "OK"
    except Exception as e:
        return False, time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:150]}"


async def test_json_schema_no_require(client: AsyncOpenAI, schema_class: type[BaseModel], task: str, user_msg: str) -> tuple[bool, float, str]:
    """Strategy 2: json_schema strict WITHOUT require_parameters."""
    schema = _prepare_schema(schema_class.model_json_schema())
    t0 = time.monotonic()
    try:
        content, fr = await _call(
            client,
            [{"role": "system", "content": task}, {"role": "user", "content": user_msg}],
            {"type": "json_schema", "json_schema": {"name": schema_class.__name__, "strict": True, "schema": schema}},
            {"provider": {"sort": "price"}},
        )
        latency = time.monotonic() - t0
        if not content:
            return False, latency, "Empty response"
        data = json.loads(_extract_json(content))
        result = schema_class.model_validate(data)
        return True, latency, _summarize(result)
    except Exception as e:
        return False, time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:150]}"


async def test_json_object_prompt(client: AsyncOpenAI, schema_class: type[BaseModel], task: str, user_msg: str) -> tuple[bool, float, str]:
    """Strategy 3: json_object + schema in prompt."""
    schema = _prepare_schema(schema_class.model_json_schema())
    schema_str = json.dumps(schema, indent=2, ensure_ascii=False)

    system_prompt = (
        f"{task}\n\n"
        "Ответь СТРОГО в JSON по следующей JSON Schema.\n"
        "Используй ТОЛЬКО поля из schema, никаких дополнительных.\n"
        "ВАЖНО: Верни ПЛОСКИЙ объект с полями из 'properties', "
        "НЕ оборачивай в {\"properties\": {...}}.\n"
        "Все обязательные поля (required) ДОЛЖНЫ присутствовать.\n"
        "Не повторяй одни и те же слова/фразы в значениях полей.\n\n"
        f"JSON Schema:\n{schema_str}"
    )

    t0 = time.monotonic()
    try:
        content, fr = await _call(
            client,
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_msg}],
            {"type": "json_object"},
            {"provider": {"sort": "price", "require_parameters": True}},
        )
        latency = time.monotonic() - t0
        if not content:
            return False, latency, f"Empty (finish_reason={fr})"
        if fr == "length":
            return False, latency, f"Truncated ({len(content)} chars)"
        data = json.loads(_extract_json(content))
        if "properties" in data and len(data) <= 2:
            inner = data.get("properties", {})
            if isinstance(inner, dict) and inner:
                data = inner
        result = schema_class.model_validate(data)
        return True, latency, _summarize(result)
    except Exception as e:
        return False, time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:150]}"


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


async def main() -> None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set")
        sys.exit(1)

    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
    schema_class, task, user_msg = QUICK_TEST

    print(f"Model: {MODEL}")
    print(f"Quick test schema: {schema_class.__name__}")
    print()

    # --- Strategy 1 ---
    print("Strategy 1: json_schema strict + require_parameters=True")
    ok1, lat1, det1 = await test_json_schema_strict(client, schema_class, task, user_msg)
    print(f"  {'PASS' if ok1 else 'FAIL'}  {lat1:.1f}s  {det1[:120]}")
    print()

    # --- Strategy 2 ---
    print("Strategy 2: json_schema strict (no require_parameters)")
    ok2, lat2, det2 = await test_json_schema_no_require(client, schema_class, task, user_msg)
    print(f"  {'PASS' if ok2 else 'FAIL'}  {lat2:.1f}s  {det2[:120]}")
    print()

    # --- Strategy 3 ---
    print("Strategy 3: json_object + schema in prompt")
    ok3, lat3, det3 = await test_json_object_prompt(client, schema_class, task, user_msg)
    print(f"  {'PASS' if ok3 else 'FAIL'}  {lat3:.1f}s  {det3[:120]}")
    print()

    # Pick best
    if ok1:
        best, fn = "json_schema strict + require_parameters", test_json_schema_strict
    elif ok2:
        best, fn = "json_schema strict (no require_parameters)", test_json_schema_no_require
    elif ok3:
        best, fn = "json_object + prompt", test_json_object_prompt
    else:
        print("=" * 80)
        print("ALL STRATEGIES FAILED")
        sys.exit(1)

    print("=" * 80)
    print(f"BEST STRATEGY: {best}")
    print("Running full suite...")
    print("-" * 80)

    results = []
    for sc, t, um in ALL_TEST_CASES:
        r_ok, r_lat, r_det = await fn(client, sc, t, um)
        print(f"  [{'PASS' if r_ok else 'FAIL'}] {sc.__name__:30s}  {r_lat:5.1f}s  {r_det[:100]}")
        results.append((sc.__name__, r_ok, r_lat))

    print("=" * 80)
    passed = sum(1 for _, r, _ in results if r)
    total = len(results)
    avg = sum(l for _, _, l in results) / total if total else 0
    print(f"Result: {passed}/{total} passed, avg latency {avg:.1f}s")
    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
