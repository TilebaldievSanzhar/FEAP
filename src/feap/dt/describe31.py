"""Генератор описания графы 31 с fact-grounding валидатором.

Каскад (SGR): facts_review → missing_attributes → text_ru + facts_used.
Harness-контроль (dt_core_dev_plan.md §4, аналог grounding read-proof):
- факты строит КОД из досье/классификации/каталога/документов, каждому — id;
- каждый facts_used обязан существовать среди выданных id;
- каждое число в тексте обязано встречаться в значениях фактов
  (цифрогруппы канонизируются; последовательности короче 2 цифр не считаем);
- нарушение → reject + retry с фидбеком, лимит DT_G31_MAX_REJECTIONS;
  после лимита текст возвращается с accepted=False — фронт не публикует
  его как готовый, человек видит причины.

Модуль только ПРЕДЛАГАЕТ текст. Применение — человек, обычным PUT досье.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from pydantic import BaseModel, Field

from feap.config import settings
from feap.dt.schemas import G31Out, ShipmentItemIn
from feap.llm.client import classify as llm_classify
from feap.prompts.registry import render

_MIN_TEXT_LEN = 20
_MAX_TEXT_LEN = 4000

_NUM_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)?")


# ── Факты ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Fact:
    id: str
    label: str
    value: str
    source: str  # 'карточка' | 'классификация' | 'каталог' | 'документ …'


def build_facts(
    item: ShipmentItemIn,
    *,
    catalog_attributes: dict | None = None,
    docs: list[dict] | None = None,
) -> list[Fact]:
    """Собрать доступные факты о позиции. Только код, никакого LLM."""
    facts: list[Fact] = []

    def add(fact_id: str, label: str, value: object, source: str) -> None:
        if value is None:
            return
        text = str(value).strip()
        if text:
            facts.append(Fact(id=fact_id, label=label, value=text, source=source))

    add("item.description", "Описание позиции", item.description, "карточка")
    if item.qty is not None:
        qty = f"{item.qty} {item.qty_unit}" if item.qty_unit else str(item.qty)
        add("item.qty", "Количество", qty, "карточка")
    add("item.net_kg", "Вес нетто, кг", item.net_kg, "карточка")
    add("item.gross_kg", "Вес брутто, кг", item.gross_kg, "карточка")
    add("item.packages", "Число мест", item.packages, "карточка")
    if item.supp_qty is not None:
        supp = f"{item.supp_qty} {item.supp_unit}" if item.supp_unit else str(item.supp_qty)
        add("item.supp_qty", "Доп. единица", supp, "карточка")
    add("item.sku", "Артикул", item.sku, "карточка")
    add("item.origin_country", "Страна происхождения", item.origin_country, "карточка")

    classification = (item.extra or {}).get("classification") or {}
    code = classification.get("final_code") or item.hs_code
    if code and re.fullmatch(r"\d{10}", str(code)):
        c = str(code)
        formatted = f"{c[:4]} {c[4:6]} {c[6:9]} {c[9]}"
        add("classification.code", "Код ТН ВЭД",
            f"{c} ({formatted})", "классификация")
    add("classification.code_description", "Описание кода ТН ВЭД",
        classification.get("code_description"), "классификация")

    for key, value in (catalog_attributes or {}).items():
        add(f"catalog.{key}", f"Каталог: {key}", value, "каталог")

    # Строки документов с ТОЧНЫМ совпадением артикула — детерминированная
    # привязка без LLM; нечёткое сопоставление — задача crosscheck.
    sku = (item.sku or "").strip().lower()
    if sku:
        for doc_idx, payload in enumerate(docs or []):
            doc = payload.get("doc") or {}
            kind = payload.get("kind")
            if kind not in ("invoice", "packing"):
                continue
            for line in doc.get("lines") or []:
                line_sku = str(line.get("sku") or "").strip().lower()
                if line_sku != sku:
                    continue
                label = f"{kind} №{doc.get('number') or '—'}, строка {line.get('line_no')}"
                add(f"doc{doc_idx}.line{line.get('line_no')}.description",
                    f"Наименование ({label})", line.get("description"), "документ")
                add(f"doc{doc_idx}.line{line.get('line_no')}.qty",
                    f"Количество ({label})", line.get("qty"), "документ")

    return facts


# ── Числовой grounding ───────────────────────────────────────────────────────

def _canon(token: str) -> str:
    """'80,00' → '80'; '05' → '5'; '12.50' → '12.5'."""
    token = token.replace(",", ".")
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    token = token.lstrip("0")
    return token or "0"


def number_tokens(text: str) -> set[str]:
    """Канонизированные цифрогруппы длиной ≥2 цифр."""
    out: set[str] = set()
    for match in _NUM_TOKEN_RE.finditer(text):
        raw = match.group(0)
        if sum(ch.isdigit() for ch in raw) < 2:
            continue
        out.add(_canon(raw))
    return out


def validate_g31(
    result: "G31Result", facts: list[Fact]
) -> list[str]:
    """Grounding-валидатор. Пустой список причин = текст принят."""
    reasons: list[str] = []
    fact_ids = {f.id for f in facts}

    unknown = [ref for ref in result.facts_used if ref not in fact_ids]
    if unknown:
        reasons.append(
            "facts_used ссылается на несуществующие факты: " + ", ".join(unknown)
        )
    if not result.facts_used:
        reasons.append("facts_used пуст — текст обязан опираться на факты досье")

    text = result.text_ru.strip()
    if len(text) < _MIN_TEXT_LEN:
        reasons.append(f"текст короче {_MIN_TEXT_LEN} символов")
    if len(text) > _MAX_TEXT_LEN:
        reasons.append(f"текст длиннее {_MAX_TEXT_LEN} символов")

    corpus: set[str] = set()
    for fact in facts:
        corpus |= number_tokens(fact.value)
    foreign = sorted(number_tokens(text) - corpus)
    if foreign:
        reasons.append(
            "в тексте есть числа, которых нет в фактах досье: " + ", ".join(foreign)
        )
    return reasons


# ── LLM-схема (уровень 93) ───────────────────────────────────────────────────

class G31Result(BaseModel):
    facts_review: str = Field(
        description="Шаг 1: какие факты доступны и что из них важно для графы 31"
    )
    missing_attributes: list[str] = Field(
        default_factory=list,
        description="Шаг 2: каких атрибутов не хватает (материал, модель, состав...)",
    )
    text_ru: str = Field(description="Шаг 3: текст графы 31 на русском")
    facts_used: list[str] = Field(
        description="id фактов, использованных в тексте (каждый обязан существовать)"
    )


async def generate_g31(
    item: ShipmentItemIn,
    *,
    catalog_attributes: dict | None = None,
    docs: list[dict] | None = None,
) -> G31Out:
    """Сгенерировать текст графы 31 с retry-циклом grounding-валидатора."""
    facts = build_facts(item, catalog_attributes=catalog_attributes, docs=docs)
    system_prompt = render("system/dt_g31.jinja2")

    max_attempts = 1 + max(0, settings.DT_G31_MAX_REJECTIONS)
    rejection_feedback: list[str] = []
    result: G31Result | None = None

    for attempt in range(1, max_attempts + 1):
        user_message = render(
            "context/dt_g31_input.jinja2",
            facts=facts,
            rejection_feedback=rejection_feedback,
        )
        result = await llm_classify(
            level=93,
            system_prompt=system_prompt,
            user_message=user_message,
            output_model=G31Result,
        )
        reasons = validate_g31(result, facts)
        if not reasons:
            return G31Out(
                accepted=True,
                text_ru=result.text_ru.strip(),
                facts=[asdict(f) for f in facts],
                facts_used=result.facts_used,
                missing_attributes=result.missing_attributes,
                attempts=attempt,
            )
        rejection_feedback = reasons

    assert result is not None
    return G31Out(
        accepted=False,
        text_ru=result.text_ru.strip(),
        facts=[asdict(f) for f in facts],
        facts_used=result.facts_used,
        missing_attributes=result.missing_attributes,
        rejection_reasons=rejection_feedback,
        attempts=max_attempts,
    )
