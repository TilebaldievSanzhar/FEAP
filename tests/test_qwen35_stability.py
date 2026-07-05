"""Stability test: 3 different products × 8 schemas = 24 calls to Qwen 3.5-27B.

Verifies that json_object + schema in prompt + /no_think works reliably
across diverse product types.
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

QWEN_MODEL = "qwen/qwen3.5-27b"
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


# --- 3 diverse products with full cascade context ---

PRODUCTS = [
    {
        "name": "Молоко сухое обезжиренное",
        "description": "Молоко сухое обезжиренное, содержание жира 1.5%, в мешках по 25 кг, для пищевой промышленности",
        "section_candidates": "I - Живые животные; продукты животного происхождения\nIV - Готовые пищевые продукты\nII - Продукты растительного происхождения",
        "section_notes": "Примечание к разделу I: включает молоко и молочную продукцию группы 04.\nПримечание к разделу IV: не включает продукты раздела I.",
        "selected_section": "I",
        "groups": "04 - Молочная продукция; яйца птиц; мёд натуральный\n05 - Продукты животного происхождения, в другом месте не поименованные",
        "group_notes": "Примечание к гр. 04: включает молоко сухое, сгущённое.",
        "selected_group": "04",
        "positions": "0401 - Молоко и сливки, несгущённые\n0402 - Молоко и сливки, сгущённые или с добавлением сахара\n0404 - Молочная сыворотка",
        "selected_position": "0402",
        "subpositions": "040210 - В порошке, гранулах, жирность не более 1.5%\n040221 - В порошке, гранулах, жирность более 1.5%\n040299 - Прочие",
        "selected_subposition": "040210",
        "codes_10": "0402100001 - Молоко сухое обезжиренное для пищевой промышленности\n0402100009 - Прочее",
    },
    {
        "name": "Шуруповёрт аккумуляторный Makita 18V",
        "description": "Шуруповёрт-дрель аккумуляторный Makita DDF484, 18V, Li-Ion, момент 54 Нм, патрон 13мм, для профессионального использования",
        "section_candidates": "XVI - Машины, оборудование и механизмы; электротехническое оборудование\nXV - Недрагоценные металлы и изделия из них\nXVII - Средства наземного транспорта",
        "section_notes": "Примечание к разделу XVI: включает ручной инструмент с электродвигателем гр. 84/85.\nПримечание к разделу XV: не включает изделия раздела XVI.",
        "selected_section": "XVI",
        "groups": "84 - Реакторы ядерные, котлы, оборудование и механические устройства\n85 - Электрические машины и оборудование, их части",
        "group_notes": "Примечание к гр. 84: включает ручной электроинструмент с электродвигателем позиции 8467.\nПримечание к гр. 85: не включает ручной электроинструмент гр. 84.",
        "selected_group": "84",
        "positions": "8467 - Инструмент ручной пневматический, гидравлический или со встроенным электрическим или неэлектрическим двигателем\n8466 - Части и принадлежности для станков\n8468 - Оборудование для пайки и сварки",
        "selected_position": "8467",
        "subpositions": "846721 - Дрели всех типов, включая перфораторы\n846729 - Прочие\n846781 - Цепные пилы\n846789 - Прочие",
        "selected_subposition": "846721",
        "codes_10": "8467211000 - С электродвигателем\n8467219000 - Прочие",
    },
    {
        "name": "Куртка мужская из натуральной кожи",
        "description": "Куртка мужская из натуральной кожи крупного рогатого скота, подкладка полиэстер 100%, застёжка-молния, размер 50, для повседневной носки",
        "section_candidates": "VIII - Необработанные шкуры, кожа, натуральный мех\nXI - Текстильные материалы и текстильные изделия\nXII - Обувь, головные уборы, зонты",
        "section_notes": "Примечание к разделу VIII: включает одежду из натуральной кожи позиции 4203.\nПримечание к разделу XI: не включает одежду из кожи (раздел VIII).",
        "selected_section": "VIII",
        "groups": "41 - Необработанные шкуры и выделанная кожа\n42 - Изделия из кожи; шорно-седельные изделия\n43 - Натуральный и искусственный мех",
        "group_notes": "Примечание к гр. 42: включает предметы одежды из натуральной кожи позиции 4203.",
        "selected_group": "42",
        "positions": "4201 - Шорно-седельные изделия\n4202 - Саквояжи, чемоданы, сумки\n4203 - Предметы одежды и принадлежности к одежде из натуральной кожи\n4205 - Изделия прочие из натуральной кожи",
        "selected_position": "4203",
        "subpositions": "420310 - Предметы одежды\n420321 - Перчатки, рукавицы специального назначения\n420329 - Перчатки прочие\n420340 - Ремни, пояса",
        "selected_subposition": "420310",
        "codes_10": "4203100001 - Куртки и пальто\n4203100009 - Прочие предметы одежды",
    },
]


def build_test_cases(product: dict) -> list[tuple[type[BaseModel], str, str]]:
    """Build all 8 schema test cases for one product."""
    p = product
    return [
        (
            ProductCard,
            "Ты эксперт по таможенной классификации. Обогати описание товара в структурированную карточку.",
            f"Товар: {p['description']}",
        ),
        (
            SectionShortlistResult,
            "Ты эксперт по ТН ВЭД ЕАЭС. Определи 2-3 наиболее вероятных раздела.",
            f"Карточка товара: {p['description']}\nРазделы ТН ВЭД:\n{p['section_candidates']}",
        ),
        (
            SectionResult,
            "Ты эксперт по ТН ВЭД ЕАЭС. Выбери один раздел, проверив примечания.",
            f"Товар: {p['description']}\n{p['section_notes']}",
        ),
        (
            GroupResult,
            f"Ты эксперт по ТН ВЭД ЕАЭС. Определи группу внутри раздела {p['selected_section']}.",
            f"Раздел: {p['selected_section']}. Товар: {p['description']}\nГруппы:\n{p['groups']}\n{p['group_notes']}",
        ),
        (
            ShortlistResult,
            f"Ты эксперт по ТН ВЭД ЕАЭС. Отбери 2-5 позиций-кандидатов в группе {p['selected_group']}.",
            f"Группа: {p['selected_group']}. Товар: {p['description']}\nПозиции:\n{p['positions']}",
        ),
        (
            PositionResult,
            "Ты эксперт по ТН ВЭД ЕАЭС. Определи позицию, применяя ОПИ 1-5.",
            f"Раздел: {p['selected_section']}, Группа: {p['selected_group']}. Товар: {p['description']}\n"
            f"Кандидаты:\n{p['positions']}",
        ),
        (
            SubpositionResult,
            f"Ты эксперт по ТН ВЭД ЕАЭС. Определи субпозицию внутри позиции {p['selected_position']}.",
            f"Позиция: {p['selected_position']}. Товар: {p['description']}\nСубпозиции:\n{p['subpositions']}",
        ),
        (
            SubsubpositionResult,
            f"Ты эксперт по ТН ВЭД ЕАЭС. Определи 10-значный код внутри субпозиции {p['selected_subposition']}.",
            f"Субпозиция: {p['selected_subposition']}. Товар: {p['description']}\nКоды:\n{p['codes_10']}",
        ),
    ]


async def test_one(
    client: AsyncOpenAI,
    schema_class: type[BaseModel],
    task: str,
    user_msg: str,
) -> tuple[str, bool, float, str]:
    name = schema_class.__name__
    schema = _prepare_schema(schema_class.model_json_schema())
    schema_str = json.dumps(schema, indent=2, ensure_ascii=False)

    system_prompt = (
        f"{task}\n\n"
        f"Верни JSON-объект, соответствующий следующей JSON Schema.\n"
        f"ВАЖНО: Верни ПЛОСКИЙ объект с полями из 'properties', НЕ оборачивай в {{'properties': ...}}.\n"
        f"Пример правильного ответа: {{\"field1\": \"value1\", \"field2\": \"value2\"}}\n"
        f"Пример НЕПРАВИЛЬНОГО ответа: {{\"properties\": {{\"field1\": \"value1\"}}}}\n"
        f"Не повторяй одни и те же слова/фразы в значениях.\n\n"
        f"JSON Schema:\n{schema_str}"
    )

    max_retries = 3
    last_error = ""
    total_latency = 0.0

    for attempt in range(1, max_retries + 1):
        # On retry after truncation, bump temperature to break repetition loops
        temp = 0.0 if attempt == 1 else 0.3
        t0 = time.monotonic()
        try:
            response = await client.chat.completions.create(
                model=QWEN_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg + " /no_think"},
                ],
                response_format={"type": "json_object"},
                extra_body={
                    "provider": {"sort": "price", "require_parameters": True},
                    "reasoning": {"effort": "none"},
                },
                temperature=temp,
                max_tokens=4096,
            )
            latency = time.monotonic() - t0
            total_latency += latency
            finish_reason = response.choices[0].finish_reason
            content = response.choices[0].message.content
            if not content:
                last_error = "Empty response"
                if attempt < max_retries:
                    print(f"    (retry {attempt}: empty response)")
                    continue
                return name, False, total_latency, last_error

            if finish_reason == "length":
                last_error = f"Truncated (finish_reason=length, {len(content)} chars)"
                if attempt < max_retries:
                    print(f"    (retry {attempt}: truncated response)")
                    continue
                return name, False, total_latency, last_error

            data = json.loads(content)
            result = schema_class.model_validate(data)
            d = result.model_dump()
            empty_required = [
                k for k, v in d.items()
                if v in (None, "", []) and k in schema.get("required", [])
            ]
            if empty_required:
                return name, False, total_latency, f"Empty required fields: {empty_required}"
            suffix = f" (retry {attempt})" if attempt > 1 else ""
            return name, True, total_latency, f"OK{suffix}"

        except Exception as e:
            latency = time.monotonic() - t0
            total_latency += latency
            last_error = f"{type(e).__name__}: {e}"
            if attempt < max_retries:
                print(f"    (retry {attempt}: {type(e).__name__})")
                continue
            return name, False, total_latency, last_error

    return name, False, total_latency, last_error


async def main() -> None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set")
        sys.exit(1)

    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)

    total_pass = 0
    total_fail = 0
    all_latencies = []

    for product in PRODUCTS:
        print(f"\n{'='*80}")
        print(f"Product: {product['name']}")
        print(f"{'='*80}")

        cases = build_test_cases(product)
        for schema_class, task, user_msg in cases:
            name, ok, latency, detail = await test_one(client, schema_class, task, user_msg)
            status = "PASS" if ok else "FAIL"
            print(f"  [{status}] {name:30s}  {latency:5.1f}s  {detail[:80]}")
            if ok:
                total_pass += 1
            else:
                total_fail += 1
            all_latencies.append(latency)

    print(f"\n{'='*80}")
    avg = sum(all_latencies) / len(all_latencies) if all_latencies else 0
    total = total_pass + total_fail
    print(f"TOTAL: {total_pass}/{total} passed, {total_fail} failed, avg latency {avg:.1f}s")
    if total_fail:
        sys.exit(1)
    print("All tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
