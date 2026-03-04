# Анализ Gemini API как альтернативного LLM-провайдера для FEAP

> Дата: 2026-03-04
> Статус: Исследование

---

## 1. Резюме

**Gemini API можно интегрировать как альтернативного провайдера.** Structured Output поддерживается нативно через Pydantic-модели, system prompts работают, Python SDK зрелый. Основные преимущества — значительно ниже стоимость (в 3-10x) и контекстное окно 1M токенов. Основной риск — менее стабильные rate limits и чуть менее предсказуемое качество на экспертных доменных задачах.

---

## 2. Structured Output — сравнение подходов

### Anthropic (текущая реализация FEAP)

```python
# Используется tool_use паттерн: LLM "вызывает функцию" со схемой
response = client.messages.create(
    model="claude-sonnet-4-6",
    system="...",
    messages=[...],
    tools=[{"name": "result", "input_schema": MyModel.model_json_schema()}],
    tool_choice={"type": "tool", "name": "result"},
)
result = MyModel.model_validate(response.content[0].input)
```

Механизм: модель генерирует `tool_use` блок, SDK извлекает JSON из аргументов.

### Gemini

```python
from google import genai
from google.genai import types

client = genai.Client(api_key="...")

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="...",
    config=types.GenerateContentConfig(
        system_instruction="...",
        response_mime_type="application/json",
        response_schema=MyModel,  # Pydantic класс напрямую
    ),
)
result: MyModel = response.parsed  # Десериализованный Pydantic-объект
```

Механизм: **constrained decoding** — генерация токенов физически ограничена JSON-схемой. Более прямой подход, без tool_use-обёртки.

### Ключевые отличия

| Аспект | Anthropic | Gemini |
|--------|-----------|--------|
| Механизм | Tool-use (инструментальный вызов) | Constrained decoding (ограниченная генерация) |
| Pydantic-интеграция | Через `model_json_schema()` вручную | Нативная: `response_schema=MyModel` |
| Доступ к результату | `response.content[0].input` → `model_validate()` | `response.parsed` (готовый объект) |
| `Optional` поля | Работают | Работают, **но `default=` запрещён** |
| Вложенные модели | Работают | Работают (с Gemini 2.5+) |
| `enum` / `Literal` | Работают | Работают |
| `anyOf` / Union | Через discriminated unions в tool_use | Заявлена поддержка, но может быть ненадёжна |

### Совместимость со схемами FEAP

Текущие Pydantic-схемы FEAP (`ProductCard`, `SectionResult`, `GroupResult`, `PositionResult` и т.д.) **в целом совместимы** с Gemini, но нужно учесть:

1. **`Field(default=...)`** — Gemini API отклоняет схемы с дефолтными значениями. Нужно убедиться, что наши схемы используют `Optional[T]` без `default`.
2. **`list[str]` / `list[Model]`** — работает в обоих API.
3. **`dict[str, float]` (material_composition_pct)** — может потребовать тестирования в Gemini.
4. **Глубоко вложенные модели** — работают, но рекомендуется тестировать конкретные схемы.

---

## 3. Модели Gemini — маппинг на уровни каскада

### Доступные модели (актуально на март 2026)

| Модель | Тип | Контекст | Цена (вход/выход за 1M токенов) |
|--------|-----|----------|--------------------------------|
| `gemini-3.1-pro-preview` | Flagship | 1M / 64k | $2.00 / $12.00 |
| `gemini-3.1-flash-lite-preview` | Budget fast | 1M / 64k | $0.25 / $1.50 |
| `gemini-3-flash-preview` | Mid-tier | 1M / 64k | $0.50 / $3.00 |
| `gemini-2.5-pro` | Reasoning | 1M / 64k | $1.25 / $10.00 |
| `gemini-2.5-flash` | Workhorse | 1M / 64k | $0.30 / $2.50 |
| `gemini-2.5-flash-lite` | Cheapest | 1M / 64k | $0.10 / $0.40 |

> **`gemini-3.1-flash-lite-preview` существует** и подходит для лёгких уровней.

### Предлагаемый маппинг

| Уровень каскада | Сложность | Anthropic (текущий) | Gemini (предлагаемый) |
|-----------------|-----------|---------------------|----------------------|
| L0 — Обогащение | Лёгкая | `claude-haiku-4-5` | `gemini-3.1-flash-lite-preview` |
| L1 — Раздел | Лёгкая | `claude-haiku-4-5` | `gemini-3.1-flash-lite-preview` |
| L2 — Группа | Средняя | `claude-sonnet-4-6` | `gemini-2.5-flash` или `gemini-3-flash-preview` |
| L3 — Позиция | Тяжёлая | `claude-opus-4-6` | `gemini-2.5-pro` или `gemini-3.1-pro-preview` |
| L4 — Субпозиция | Средняя | `claude-sonnet-4-6` | `gemini-2.5-flash` |
| L5 — Подсубпозиция | Лёгкая | `claude-haiku-4-5` | `gemini-3.1-flash-lite-preview` |

### Сравнение стоимости (за 1M токенов)

| Уровень | Anthropic (вход/выход) | Gemini (вход/выход) | Экономия |
|---------|----------------------|--------------------|-----------|
| L0, L1, L5 | Haiku: $1.00 / $5.00 | flash-lite: $0.25 / $1.50 | **~4x / ~3x** |
| L2, L4 | Sonnet: $3.00 / $15.00 | flash: $0.30 / $2.50 | **~10x / ~6x** |
| L3 | Opus: $15.00 / $75.00 | 2.5 Pro: $1.25 / $10.00 | **~12x / ~7.5x** |

> Суммарная экономия на полный каскад может составить **5-10x** при переходе на Gemini.

---

## 4. Python SDK

### Установка

```bash
pip install google-genai
```

> **Важно:** старый пакет `google-generativeai` **deprecated**. Используем только `google-genai`.

### API-вызов с system prompt + structured output

```python
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=[
        types.Content(role="user", parts=[types.Part(text="Карточка товара: ...")])
    ],
    config=types.GenerateContentConfig(
        system_instruction="Ты — эксперт по классификации товаров по ТН ВЭД ЕАЭС...",
        temperature=1.0,  # Gemini 3 рекомендует не снижать для reasoning
        max_output_tokens=4096,
        response_mime_type="application/json",
        response_schema=SectionResult,
    ),
)

result: SectionResult = response.parsed

# Метаданные использования
print(response.usage_metadata.prompt_token_count)
print(response.usage_metadata.candidates_token_count)
```

### Async-поддержка

```python
# google-genai поддерживает async через aio-версии
client = genai.Client(api_key="...")
response = await client.aio.models.generate_content(
    model="gemini-2.5-flash",
    contents="...",
    config=types.GenerateContentConfig(...)
)
```

---

## 5. System Prompts

Полностью поддерживаются. Передаются через `system_instruction` в `GenerateContentConfig`:

```python
config=types.GenerateContentConfig(
    system_instruction="Ты — эксперт по таможенной классификации...",
    # Или список строк:
    system_instruction=["Инструкция 1", "Инструкция 2"],
)
```

Аналогично параметру `system` в Anthropic API. Jinja2-шаблоны FEAP можно использовать без изменений — шаблоны генерируют строки, которые передаются в оба API одинаково.

---

## 6. Контекстное окно

| Параметр | Anthropic | Gemini |
|----------|-----------|--------|
| Макс. ввод | 200k (1M для Opus beta) | **1M** (все модели) |
| Макс. вывод | 8k-16k | **64k** |

Для FEAP это не критично (наши промпты обычно 5-30 KB), но на L3 с RAG-пояснениями запас Gemini удобнее.

---

## 7. Риски и ограничения

### 7.1. Rate Limits — основной риск

С декабря 2025 у Gemini 2.5 моделей зафиксирована проблема **"ghost 429"** — ошибки rate limit при неиспользованной квоте (GitHub Issue #4500, P0 у Google).

**Митигация:** агрессивная retry-логика (exponential backoff, до 10 попыток) + fallback на Anthropic.

### 7.2. `Field(default=...)` запрещён

API отклоняет Pydantic-схемы с дефолтными значениями полей. Нужно проверить все наши схемы.

### 7.3. `response.parsed` возвращает `None` без ошибки

Если `max_output_tokens` превышен, ответ обрезается и `parsed` = `None`. Нужна обязательная проверка.

### 7.4. Качество на доменных задачах

Для экспертной таможенной классификации на русском языке Claude исторически показывает более стабильные результаты. Рекомендуется провести **A/B-тестирование** на 50-100 реальных товарах перед переключением L3 на Gemini.

### 7.5. Temperature

Gemini 3 модели по умолчанию используют `temperature=1.0`. Google предупреждает: **не снижайте** для reasoning-задач — это может ухудшить качество. Контринтуитивно, но важно.

### 7.6. Preview-модели

`gemini-3.1-flash-lite-preview` — preview-модель. Может измениться без уведомления. Для production рекомендуется `gemini-2.5-flash-lite` (стабильная).

### 7.7. Совместимость SDK с Python 3.14

Текущий проект использует Python 3.14. Нужно проверить, поддерживает ли `google-genai` эту версию (вероятно да, но требуется тест).

---

## 8. Архитектурное решение — абстракция провайдера

### Предлагаемый подход

Ввести абстрактный `LLMProvider` интерфейс, за которым скрыты конкретные реализации:

```
src/feap/llm/
├── provider.py          # ABC: LLMProvider
├── anthropic_provider.py  # AnthropicProvider (текущий)
├── gemini_provider.py     # GeminiProvider (новый)
└── factory.py           # create_provider(name) → LLMProvider
```

### Конфигурация через .env

```env
# Выбор провайдера
LLM_PROVIDER=anthropic          # или "gemini"

# Anthropic (текущие)
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL_LIGHT=claude-haiku-4-5-20251001
LLM_MODEL_MEDIUM=claude-sonnet-4-6
LLM_MODEL_HEAVY=claude-opus-4-6

# Gemini (новые)
GEMINI_API_KEY=AI...
LLM_MODEL_LIGHT_GEMINI=gemini-3.1-flash-lite-preview
LLM_MODEL_MEDIUM_GEMINI=gemini-2.5-flash
LLM_MODEL_HEAVY_GEMINI=gemini-2.5-pro
```

### Интерфейс провайдера

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import Type, TypeVar

T = TypeVar("T", bound=BaseModel)

class LLMProvider(ABC):
    @abstractmethod
    async def generate_structured(
        self,
        model_tier: str,           # "light" | "medium" | "heavy"
        system_prompt: str,
        user_message: str,
        response_model: Type[T],
    ) -> T:
        """Генерация с гарантированным structured output."""
        ...

    @abstractmethod
    async def generate_text(
        self,
        model_tier: str,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """Простая текстовая генерация."""
        ...
```

### Что менять в каскаде

Оркестратор и уровни каскада вызывают `provider.generate_structured(...)` вместо прямого обращения к Anthropic SDK. Промпты и Pydantic-схемы остаются **без изменений**.

### Можно ли миксовать провайдеров?

Да. Вариант «per-level» конфигурации:

```env
# L0-L2, L5 на дешёвом Gemini, L3-L4 на Claude
LLM_PROVIDER_L0=gemini
LLM_PROVIDER_L1=gemini
LLM_PROVIDER_L2=gemini
LLM_PROVIDER_L3=anthropic
LLM_PROVIDER_L4=anthropic
LLM_PROVIDER_L5=gemini
```

Это позволит использовать Gemini для дешёвых уровней и Claude для критичного L3.

---

## 9. Итоговая оценка

| Критерий | Оценка | Комментарий |
|----------|--------|-------------|
| Structured Output | **Отлично** | Нативная поддержка Pydantic, `response.parsed` |
| System Prompts | **Отлично** | `system_instruction` — аналог Anthropic `system` |
| Python SDK | **Хорошо** | `google-genai`, async поддержка, нужна проверка на Python 3.14 |
| Стоимость | **Отлично** | 3-12x дешевле Anthropic на сопоставимых моделях |
| Контекстное окно | **Отлично** | 1M вход / 64k выход у всех моделей |
| Rate Limits | **Риск** | Документированные "ghost 429" ошибки, нужен retry + fallback |
| Качество (рус. язык) | **Требует тестирования** | Нужен A/B-тест на реальных товарах, особенно L3 |
| Совместимость схем | **Хорошо** | Нужно убрать `Field(default=...)`, проверить `dict` типы |
| Стабильность моделей | **Средне** | Preview-модели могут меняться; 2.5 серия стабильна |
| Интеграционная сложность | **Средняя** | ~2-3 дня на абстракцию провайдера + GeminiProvider |

### Рекомендация

**Да, интеграция Gemini целесообразна.** Рекомендуемый план:

1. Создать абстракцию `LLMProvider` с двумя реализациями
2. Начать с Gemini на лёгких уровнях (L0, L1, L5) — низкий риск, высокая экономия
3. Провести A/B-тест на L3 (позиция) — критичный уровень
4. Реализовать fallback: при 429 от Gemini → переключаться на Anthropic
5. Per-level конфигурация провайдера — максимальная гибкость

---

## Источники

- [Gemini API — Structured Output](https://ai.google.dev/gemini-api/docs/structured-output)
- [Gemini API — Models](https://ai.google.dev/gemini-api/docs/models)
- [Gemini API — Pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini API — Rate Limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [google-genai SDK (GitHub)](https://github.com/googleapis/python-genai)
- [Ghost 429 Issue (GitHub #4500)](https://github.com/googleapis/python-genai/issues/4500)
- [Pydantic default values issue (GitHub #699)](https://github.com/googleapis/python-genai/issues/699)
