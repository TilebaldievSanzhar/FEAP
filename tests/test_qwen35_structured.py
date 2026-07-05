"""Test Qwen 3.5-27B structured output via json_object + schema in prompt + /no_think.

Validates that all 7 cascade schemas produce valid Pydantic models
when using the approach described in docs/structured_output_providers.md.

Usage:
    python tests/test_qwen35_structured.py
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

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from feap.schemas.product_card import ProductCard
from feap.schemas.section import SectionShortlistResult, SectionResult
from feap.schemas.group import GroupResult
from feap.schemas.position import ShortlistResult, PositionResult
from feap.schemas.subposition import SubpositionResult
from feap.schemas.subsubposition import SubsubpositionResult

load_dotenv()

QWEN_MODEL = "qwen/qwen3.5-27b"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# --- Schema prep (same as openrouter_provider.py) ---

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


# --- Test cases: (schema_class, system_prompt_task, user_message) ---

PRODUCT = "Электрическая зубная щётка Oral-B Pro 3000 с аккумулятором Li-Ion, 3.7V"

TEST_CASES: list[tuple[type[BaseModel], str, str]] = [
    (
        ProductCard,
        "Ты эксперт по таможенной классификации. Обогати описание товара в структурированную карточку.",
        f"Товар: {PRODUCT}",
    ),
    (
        SectionShortlistResult,
        "Ты эксперт по ТН ВЭД ЕАЭС. Определи 2-3 наиболее вероятных раздела для товара.",
        f"Карточка товара: электрическая зубная щётка, бытовое назначение, встроенный электродвигатель, аккумулятор Li-Ion.\n"
        f"Разделы ТН ВЭД:\n"
        f"XVI - Машины, оборудование и механизмы; электротехническое оборудование\n"
        f"XVIII - Инструменты и аппараты оптические, фотографические...\n"
        f"XX - Разные промышленные товары\n"
        f"VI - Продукция химической и связанных с ней отраслей промышленности",
    ),
    (
        SectionResult,
        "Ты эксперт по ТН ВЭД ЕАЭС. Выбери один раздел, проверив примечания.",
        f"Карточка товара: электрическая зубная щётка с электродвигателем.\n"
        f"Кандидаты: XVI, XX\n"
        f"Примечание к разделу XVI: включает электромеханические бытовые приборы группы 85.\n"
        f"Примечание к разделу XX: не включает товары раздела XVI.",
    ),
    (
        GroupResult,
        "Ты эксперт по ТН ВЭД ЕАЭС. Определи группу внутри раздела XVI.",
        f"Раздел: XVI. Товар: электрическая зубная щётка.\n"
        f"Группы раздела XVI:\n"
        f"84 - Реакторы ядерные, котлы, оборудование и механические устройства\n"
        f"85 - Электрические машины и оборудование, их части\n"
        f"Примечание к гр. 85: включает электромеханические бытовые машины товарной позиции 8509.",
    ),
    (
        ShortlistResult,
        "Ты эксперт по ТН ВЭД ЕАЭС. Отбери 2-5 позиций-кандидатов в группе 85.",
        f"Группа: 85. Товар: электрическая зубная щётка бытовая.\n"
        f"Позиции:\n"
        f"8509 - Машины электромеханические бытовые со встроенным электродвигателем\n"
        f"8510 - Электробритвы, машинки для стрижки волос\n"
        f"8516 - Электрические водонагреватели, приборы для обогрева...",
    ),
    (
        PositionResult,
        "Ты эксперт по ТН ВЭД ЕАЭС. Определи позицию, применяя ОПИ 1-5.",
        f"Раздел: XVI, Группа: 85. Товар: электрическая зубная щётка бытовая.\n"
        f"Кандидаты: 8509, 8510\n"
        f"8509 — Машины электромеханические бытовые со встроенным электродвигателем\n"
        f"8510 — Электробритвы, машинки для стрижки и аналогичные электроприборы\n"
        f"Пояснение к 8509: включает зубные щётки электрические.",
    ),
    (
        SubpositionResult,
        "Ты эксперт по ТН ВЭД ЕАЭС. Определи субпозицию (6 цифр) внутри позиции 8509.",
        f"Позиция: 8509. Товар: электрическая зубная щётка.\n"
        f"Субпозиции:\n"
        f"850940 - Измельчители пищевых продуктов и миксеры для продуктов\n"
        f"850980 - Прочие машины\n"
        f"ОПИ 6: субпозиции одного уровня сравниваются между собой.",
    ),
    (
        SubsubpositionResult,
        "Ты эксперт по ТН ВЭД ЕАЭС. Определи 10-значный код внутри субпозиции 850980.",
        f"Субпозиция: 850980. Товар: электрическая зубная щётка.\n"
        f"Коды:\n"
        f"8509800001 - Щётки зубные электрические\n"
        f"8509800009 - Прочие",
    ),
]


async def test_schema(
    client: AsyncOpenAI,
    schema_class: type[BaseModel],
    task: str,
    user_msg: str,
) -> tuple[str, bool, float, str]:
    """Test one schema. Returns (name, success, latency, detail)."""
    name = schema_class.__name__
    schema = _prepare_schema(schema_class.model_json_schema())
    schema_str = json.dumps(schema, indent=2, ensure_ascii=False)

    system_prompt = (
        f"{task}\n\n"
        f"Ответь СТРОГО в JSON по следующей JSON Schema.\n"
        f"Используй ТОЛЬКО поля из schema, никаких дополнительных.\n"
        f"Все обязательные поля (required) ДОЛЖНЫ присутствовать.\n\n"
        f"JSON Schema:\n{schema_str}"
    )
    user_message = user_msg + " /no_think"

    t0 = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=QWEN_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            extra_body={
                "provider": {
                    "sort": "price",
                    "require_parameters": True,
                },
                "reasoning": {"effort": "none"},
            },
            temperature=0.0,
            max_tokens=4096,
        )
        latency = time.monotonic() - t0
        content = response.choices[0].message.content
        if not content:
            return name, False, latency, "Empty response"

        data = json.loads(content)
        result = schema_class.model_validate(data)

        # Quick sanity check on key fields
        detail = _summarize(result)
        return name, True, latency, detail

    except Exception as e:
        latency = time.monotonic() - t0
        return name, False, latency, f"{type(e).__name__}: {e}"


def _summarize(obj: BaseModel) -> str:
    """Extract a brief summary of the parsed result."""
    d = obj.model_dump(exclude_defaults=True)
    # Keep only a few representative fields
    keys = list(d.keys())[:5]
    summary_parts = []
    for k in keys:
        v = d[k]
        if isinstance(v, str) and len(v) > 80:
            v = v[:80] + "..."
        elif isinstance(v, list) and len(v) > 2:
            v = f"[{len(v)} items]"
        summary_parts.append(f"{k}={v!r}")
    return ", ".join(summary_parts)


async def main() -> None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set")
        sys.exit(1)

    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)

    print(f"Testing {QWEN_MODEL} with json_object + schema in prompt + /no_think")
    print(f"Schemas to test: {len(TEST_CASES)}")
    print("=" * 80)

    results = []
    for schema_class, task, user_msg in TEST_CASES:
        name, ok, latency, detail = await test_schema(client, schema_class, task, user_msg)
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name:30s}  {latency:5.1f}s  {detail[:100]}")
        results.append((name, ok, latency))

    print("=" * 80)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    avg_latency = sum(lat for _, _, lat in results) / total if total else 0
    print(f"Result: {passed}/{total} passed, avg latency {avg_latency:.1f}s")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
