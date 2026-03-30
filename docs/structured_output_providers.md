# Structured Output: совместимость моделей и провайдеров через OpenRouter

## Проблема

Не все модели/провайдеры на OpenRouter одинаково поддерживают `response_format: { type: "json_schema", strict: true }`.
Есть три уровня поддержки:

1. **Grammar-constrained decoding** — провайдер на уровне токенов гарантирует соответствие schema (Anthropic, OpenAI, Fireworks, DeepInfra для некоторых моделей). Работает надёжно.
2. **Schema-as-hint** — провайдер передаёт schema модели, но не enforc'ит. Модель может вернуть JSON со своими полями. Поле `structured_outputs` в OpenRouter API = `true`, но enforcement отсутствует.
3. **Не поддерживается** — провайдер игнорирует `response_format` полностью.

`require_parameters: true` в `provider` настройках гарантирует роутинг на провайдеров уровня 1 или 2, но **не различает** между ними.

## Конкретные модели

### Anthropic Claude, OpenAI GPT, DeepSeek V3.2

- `json_schema` + `strict: true` — **работает** (grammar-constrained decoding)
- Стандартная стратегия `_classify_strict_schema` в `OpenRouterProvider`
- `reasoning: {effort: "none"}` — отключает reasoning для гибридных моделей (DeepSeek)

### DeepSeek V3.2 (`deepseek/deepseek-v3.2`)
- `json_schema` + `strict: true` — **работает** (grammar-constrained у провайдеров AtlasCloud и др.)
- Гибридная модель (thinking/non-thinking). Reasoning отключается через `reasoning: {effort: "none"}`
- Рекомендуемые настройки:
  ```python
  response_format={"type": "json_schema", "json_schema": {"name": "...", "strict": True, "schema": prepared_schema}}
  extra_body={"provider": {"sort": "price", "require_parameters": True}, "reasoning": {"effort": "none"}}
  ```

### MiniMax M2.7 (`minimax/minimax-m2.7`)

- `json_schema` + `strict: true` + `require_parameters` — **404**: ни один провайдер не поддерживает grammar-constrained decoding
- `json_schema` + `strict: true` без `require_parameters` — провайдер принимает запрос, но модель **игнорирует schema** и возвращает markdown-текст
- `json_object` + schema в промпте — **работает** (8/8 схем каскада прошли)

#### Особенности MiniMax M2.7

1. **Mandatory reasoning**: модель всегда генерирует `<think>...</think>` токены. Параметр `reasoning: {effort: "none"}` **отклоняется** с ошибкой `400 Bad Request: "Reasoning is mandatory for this endpoint and cannot be disabled"`. Thinking-токены передаются в отдельном поле `reasoning_content`, **не** в `message.content`.

2. **Markdown fences**: при `json_object` модель оборачивает JSON в ` ```json ... ``` `. Нужно стрипать markdown-обёртку перед парсингом (`_extract_json()` в provider).

3. **Один провайдер на OpenRouter**: MiniMax (slug: `minimax/fp8`, FP8 квантизация). Суффиксы `:nitro`/`:floor` не дают эффекта при единственном провайдере.

4. **Highspeed вариант** (`minimax-m2.7-highspeed`):
   - **Не доступен на OpenRouter** — только через прямой API MiniMax (`https://api.minimaxi.chat/v1`)
   - Цена: $0.60/M input, $2.40/M output (2x от стандартного)
   - Скорость: ~100 TPS vs ~60 TPS — тот же вес модели, но инференс на более мощном железе
   - Для интеграции потребуется отдельный provider или BYOK

#### Рабочий подход для MiniMax M2.7

Используется `json_object` + schema в промпте (аналогично Qwen 3.5, но без `/no_think`):

```python
# 1. Подготовить schema как текст
schema = _prepare_schema(OutputModel.model_json_schema())
schema_str = json.dumps(schema, indent=2, ensure_ascii=False)

# 2. Вставить schema в system prompt
system_prompt = f"""{original_system_prompt}

Верни JSON-объект, соответствующий следующей JSON Schema.
ВАЖНО: Верни ПЛОСКИЙ объект с полями из 'properties', НЕ оборачивай в {{"properties": {{...}}}}.

JSON Schema:
{schema_str}"""

# 3. НЕ добавлять /no_think (это Qwen-специфично, MiniMax не поддерживает)

# 4. Использовать json_object вместо json_schema
# 5. НЕ отправлять reasoning: {effort: "none"} — MiniMax отклонит
response = await client.chat.completions.create(
    model="minimax/minimax-m2.7",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ],
    response_format={"type": "json_object"},
    extra_body={
        "provider": {"sort": "price", "require_parameters": True},
        # НЕТ "reasoning": {"effort": "none"} — mandatory reasoning!
    },
    temperature=0.0,
    max_tokens=4096,
)

# 6. Стрипнуть markdown fences и распарсить
content = response.choices[0].message.content
content = _extract_json(content)  # убирает ```json ... ```
data = json.loads(content)
result = OutputModel.model_validate(data)
```

**Результат тестирования:** 8/8 schema каскада (ProductCard, SectionShortlistResult, SectionResult, GroupResult, ShortlistResult, PositionResult, SubpositionResult, SubsubpositionResult) — все прошли. Avg latency: ~25с (включая mandatory thinking).

### Qwen 3.5-27B (`qwen/qwen3.5-27b`)
- `json_schema` + `strict: true` — **НЕ работает**. Все провайдеры (Alibaba, AtlasCloud, Venice) — уровень 2, schema игнорируется.
- Причина: thinking-модель, и [по документации Alibaba](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output): *"Models in thinking mode do not support structured output"*.
- Параметр `reasoning: {effort: "none"}` через OpenRouter **недостаточен** — провайдеры не всегда транслируют его корректно.

#### Рабочий подход для Qwen 3.5

Использовать `json_object` + schema в промпте + `/no_think`:

```python
# 1. Подготовить schema как текст
schema = _prepare_schema(OutputModel.model_json_schema())
schema_str = json.dumps(schema, indent=2, ensure_ascii=False)

# 2. Вставить schema в system prompt
system_prompt = f"""Ты эксперт по ТН ВЭД ЕАЭС. Ответь СТРОГО в JSON по следующей JSON Schema.
Используй ТОЛЬКО поля из schema, никаких дополнительных.

JSON Schema:
{schema_str}"""

# 3. Добавить /no_think в конец user message
user_message = original_prompt + " /no_think"

# 4. Использовать json_object вместо json_schema
response = await client.chat.completions.create(
    model="qwen/qwen3.5-27b",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ],
    response_format={"type": "json_object"},  # НЕ json_schema!
    extra_body={
        "provider": {"sort": "price", "require_parameters": True},
        "reasoning": {"effort": "none"},
    },
    temperature=0.0,
    max_tokens=4096,
)

# 5. Парсинг и валидация
data = json.loads(response.choices[0].message.content)
result = OutputModel.model_validate(data)
```

**Почему это работает:**
- `/no_think` — нативный Qwen-токен, отключает thinking на уровне модели (не зависит от провайдера)
- `json_object` — гарантирует валидный JSON (без markdown, без лишнего текста)
- Schema в промпте — модель следует инструкциям (Qwen 3.5 имеет IFEval score 95.0 — отличное следование инструкциям)
- Валидация через Pydantic ловит отклонения

**Результат тестирования:** 7/7 schema каскада (ProductCard, SectionShortlistResult, SectionResult, GroupResult, ShortlistResult, PositionResult, SubpositionResult) — все прошли. Latency: 3-11с.

### Qwen 3 235B MoE (`qwen/qwen3-235b-a22b-2507`)
- `json_schema` + `strict: true` — **работает** (провайдеры WandB, Parasail, Novita делают constrained decoding)
- Не thinking-модель — `reasoning: {effort: "none"}` не нужен, но безвреден
- Latency: высокая (~100-140с на полный каскад), что делает бенчмаркинг дорогим по времени

## Сводная таблица моделей

| Модель | Стратегия | Reasoning | Markdown fences | Latency/вызов |
|--------|-----------|-----------|-----------------|---------------|
| Anthropic Claude | `json_schema` strict | отключаем | нет | ~3-8с |
| DeepSeek V3.2 | `json_schema` strict | отключаем | нет | ~3-6с |
| Qwen 3-235B | `json_schema` strict | не нужен | нет | ~12-18с |
| Qwen 3.5-27B | `json_object` + prompt + `/no_think` | отключаем | нет | ~3-11с |
| MiniMax M2.7 | `json_object` + prompt | **mandatory** | **да** (`\`\`\`json`) | ~16-35с |

## Общие рекомендации

### Provider routing
```python
extra_body = {
    "provider": {
        "sort": "price",            # самый дешёвый провайдер
        "require_parameters": True,  # только те, кто поддерживает наши параметры
    }
}
```

### Отключение reasoning для гибридных моделей
```python
# Для моделей, где reasoning отключаем (DeepSeek, Qwen и др.)
extra_body["reasoning"] = {"effort": "none"}

# Для MiniMax — НЕ отправлять этот параметр (mandatory reasoning)
```
Для Qwen 3.5 дополнительно: `/no_think` в конце user message.

### Известные баги в экосистеме
- [vLLM #18819](https://github.com/vllm-project/vllm/issues/18819) — structured output сломан при `enable_thinking=False`, workaround: `/no_think`
- [vLLM #23404](https://github.com/vllm-project/vllm/issues/23404) — Qwen3 игнорирует field descriptions в schema
- [HuggingFace discussion](https://huggingface.co/Qwen/Qwen3.5-35B-A3B/discussions/18) — reasoning content утекает в `message.content`
- [sglang #6675](https://github.com/sgl-project/sglang/issues/6675) — structured output сломан с thinking-моделями

## Имплементация в FEAP

`OpenRouterProvider` (`src/feap/llm/openrouter_provider.py`) автоматически выбирает стратегию:

| Модель | Стратегия | Детали |
|--------|-----------|--------|
| Anthropic, OpenAI, DeepSeek, Qwen3-235B | `json_schema` strict | Grammar-constrained decoding |
| MiniMax M2.* | `json_object` + prompt | Schema в system prompt, markdown fence stripping |
| Qwen 3.5-* | `json_object` + prompt | Schema в system prompt + `/no_think` |

Конфигурация определяется двумя списками префиксов в `openrouter_provider.py`:
- `_PROMPT_SCHEMA_MODEL_PREFIXES` — модели, требующие prompt-based стратегию
- `_MANDATORY_REASONING_PREFIXES` — модели, которым нельзя отправлять `reasoning: {effort: "none"}`

### Защита от repetition loops

При `json_object` без grammar-constrained decoding модель может зациклиться на повторении фраз (особенно в строковых полях). Защита:
- Детекция `finish_reason=length` (обрезанный ответ = вероятный loop)
- Retry с повышенной temperature (0.3) для выхода из детерминированного цикла
- Максимум 3 попытки
- Инструкция «Не повторяй одни и те же слова/фразы в значениях полей»

### Защита от {"properties": {...}} обёртки

Некоторые модели при получении JSON Schema буквально воспроизводят её структуру. Защита:
- Явная инструкция: «Верни ПЛОСКИЙ объект с полями из properties»
- Пример правильного и неправильного ответа в промпте
- Fallback unwrap: если ответ содержит `{"properties": {...}}`, извлекается inner dict

### Защита от markdown fences

Некоторые модели (MiniMax) оборачивают JSON-ответ в ` ```json ... ``` ` даже при `response_format: json_object`. Защита:
- Regex-стрипинг ` ```json ... ``` ` перед `json.loads()` (`_extract_json()`)
- Применяется только в prompt-based стратегии (grammar-constrained decoding не допускает fences)

## Ценообразование (на момент исследования)

| Модель | Input $/M | Output $/M | Structured output | Метод | Дата |
|--------|-----------|------------|-------------------|-------|------|
| google/gemini-2.0-flash-lite-001 | $0.075 | $0.30 | json_schema strict | Нативный | 2026-03 |
| deepseek/deepseek-v3.2 | $0.26 | $0.38 | json_schema strict | Через провайдеров | 2026-03 |
| qwen/qwen3.5-27b | $0.195 | $1.56 | json_object + prompt | /no_think + schema в промпте | 2026-03 |
| qwen/qwen3-235b-a22b-2507 | $0.071 | $0.10 | json_schema strict | Через провайдеров | 2026-03 |
| minimax/minimax-m2.7 | $0.30 | $1.20 | json_object + prompt | Schema в промпте + fence strip | 2026-03 |
| minimax/minimax-m2.7-highspeed | $0.60 | $2.40 | (не на OpenRouter) | Прямой API MiniMax | 2026-03 |
