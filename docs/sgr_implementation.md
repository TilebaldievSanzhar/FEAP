# Schema-Guided Reasoning (SGR) для каскадного классификатора ТН ВЭД

**Дата внедрения**: 2026-03-21
**Статус**: Реализовано, готово к бенчмарку

---

## 1. Проблема

После бенчмарка v8 стала очевидна дилемма «качество vs стоимость»:

| Модель | Heading accuracy | Code-10 accuracy | $/классификация | Thinking tokens |
|--------|:---:|:---:|:---:|:---:|
| gemini-3.1-flash-lite (v6) | 80% | 62% | $0.015 | 0 (нет thinking) |
| gemini-2.5-flash (v8) | 94% | 73% | $0.033 | ~6,400 (48% стоимости) |

**flash-lite** дешёвая, но не следует сложным инструкциям в промптах — игнорирует правила ОПИ, не сопоставляет числовые пороги, выдаёт ложные `needs_input`.

**2.5-flash** качественная, но дорогая — модель «думает» внутри (thinking tokens по $2.50/MTok), и эти размышления не контролируемы: мы не знаем, о чём модель думает и насколько эффективно.

**Вопрос**: Можно ли направлять рассуждения модели **извне** через структуру схемы, чтобы:
- flash-lite получила «скелет мышления» и стала качественнее?
- 2.5-flash меньше тратила на внутренний thinking, получая «план мышления» через структуру?

---

## 2. Концепция SGR

### 2.1 Что такое Schema-Guided Reasoning

SGR (Schema-Guided Reasoning) — техника, при которой **структура выходной JSON-схемы направляет рассуждения LLM**. Вместо свободного «подумай и дай ответ» модель заполняет поля в строгом порядке, где аналитические поля идут **до** полей с решением.

**Источник**: [abdullin.com/schema-guided-reasoning](https://abdullin.com/schema-guided-reasoning/)

### 2.2 Почему это работает

При использовании **constrained decoding** (Gemini `response_json_schema`) модель генерирует JSON-поля **последовательно**, в порядке их определения в схеме. Это означает:

1. Если поле `step1_product_nature` идёт **перед** `selected_position`, модель **вынуждена** заполнить анализ физической природы товара прежде, чем выбрать позицию.
2. Каждое аналитическое поле — это «контрольная точка мышления»: модель не может перепрыгнуть к ответу, не пройдя через все шаги.
3. **Имена полей имеют значение** — они получают повышенное внимание модели при генерации. Поле `threshold_comparison` буквально заставляет модель искать и сравнивать пороги.

### 2.3 Аналогия

| Без SGR | С SGR |
|---------|-------|
| Студент «подумал в уме» и написал ответ | Студент заполняет **бланк эксперта** с пошаговыми полями |
| Невозможно проверить ход рассуждений | Каждый шаг виден и проверяем |
| Слабый студент пропускает шаги | Слабый студент вынужден пройти все шаги |

### 2.4 SGR Cascade Pattern

Основной паттерн SGR, применённый в проекте — **Cascade** (каскад полей):

```
[Аналитическое поле 1] → [Аналитическое поле 2] → ... → [Поле решения]
```

Поля упорядочены так, чтобы каждое следующее **уточняло и фокусировало** информацию, постепенно приближаясь к финальному решению. Это прямая аналогия с тем, как рассуждает эксперт-таможенник:

1. Определи **что это** (материал, форма)
2. Сопоставь с **текстами позиций**
3. Разреши **неоднозначности** (ОПИ 2/3)
4. Только потом — **выбери код**

---

## 3. Что сделано

### 3.1 Обзор изменений

| Компонент | Файлов | Суть |
|-----------|:------:|------|
| Pydantic-схемы | 6 | SGR-поля добавлены перед decision-полями |
| System-промпты | 8 | Секция «ПОРЯДОК АНАЛИЗА» с инструкциями |
| LLM-инфраструктура | 3 | Per-level thinking budget |
| Level handlers | 0 | **Не изменены** — SGR-поля игнорируются бизнес-логикой |

**Ключевое**: SGR-поля заполняются моделью для направления её рассуждений, но **не используются в коде**. Бизнес-логика по-прежнему работает с `selected_*`, `confidence`, `missing_parameters`, `backtrack_trigger`.

### 3.2 SGR-поля по уровням

#### L0 — Обогащение описания (`product_card.py`)

```python
class ProductCard(BaseModel):
    original_description: str
    # --- SGR ---
    product_identification: str       # «Что это за товар? Тип, категория, назначение»
    classification_relevant_features: str  # «Какие характеристики важны для классификации»
    # --- Затем обычные поля ---
    normalized_description: str
    material: str | None
    ...
```

**Зачем**: Прежде чем заполнять 15+ полей карточки, модель должна определить *что это за товар* и *что важно для классификации*. Это предотвращает бессмысленное заполнение (например, поиск «мощности» у ткани).

#### L1a — Shortlist разделов (`section.py`)

```python
class SectionShortlistResult(BaseModel):
    product_nature: str          # «Физическая природа: материал, форма, назначение»
    primary_section_signal: str  # «Главный признак → раздел»
    shortlisted: list[str]       # Решение: 2-3 раздела
    reasoning: str
```

**Зачем**: Модель сначала определяет физическую природу товара (это «стальная труба», а не «элемент трубопровода»), затем выделяет главный сигнал (сталь → Раздел XV), и только потом выбирает кандидатов.

#### L1b — Верификация с примечаниями (`section.py`)

```python
class SectionCandidate(BaseModel):
    section: str
    section_name: str
    text_match_analysis: str  # «Как текст раздела соотносится с товаром»  ← NEW
    exclusion_check: str
    reasoning: str
    confidence: float         # Последним — после всего анализа

class SectionResult(BaseModel):
    key_notes_analysis: str   # «Ключевые примечания, повлиявшие на решение»  ← NEW
    candidates: list[SectionCandidate]
    selected_section: str
    ...
```

**Зачем**: `key_notes_analysis` заставляет модель перечитать примечания перед выбором. `text_match_analysis` в каждом кандидате — явное сопоставление текста раздела с товаром, а не просто «reasoning» в свободной форме.

#### L2 — Группа (`group.py`)

```python
class GroupCandidate(BaseModel):
    group: str
    group_name: str
    text_match_analysis: str   # «Соответствие текста группы товару»  ← NEW
    exclusion_check: str
    reasoning: str
    confidence: float

class GroupResult(BaseModel):
    section: str
    product_in_section_context: str  # «Как товар вписывается в раздел»  ← NEW
    key_exclusion_notes: str         # «Какие исключения проверены»  ← NEW
    candidates: list[GroupCandidate]
    selected_group: str
    ...
```

**Зачем**: `product_in_section_context` заставляет модель подумать, как товар соотносится с разделом в целом, прежде чем выбирать конкретную группу. `key_exclusion_notes` — явная фиксация проверенных исключений (это было проблемой в v6/v7 — модель пропускала исключающие примечания).

#### L3a — Shortlist позиций (`position.py`)

```python
class ShortlistResult(BaseModel):
    group: str
    physical_nature_analysis: str  # «Материал, форма, степень обработки»  ← NEW
    key_differentiators: str       # «По каким критериям различаются позиции»  ← NEW
    shortlisted: list[str]
    reasoning: str
```

**Зачем**: Самый важный уровень. `physical_nature_analysis` закрепляет принцип «классифицируй по физической природе, не по назначению» (проблема P5 из v7). `key_differentiators` заставляет модель понять *структуру группы* перед выбором кандидатов.

#### L3b — Глубокий анализ позиций (`position.py`) — **ГЛАВНЫЙ УРОВЕНЬ**

```python
class PositionCandidate(BaseModel):
    position: str
    position_text: str
    text_match_score: str   # «exact/partial/weak»  ← NEW
    reasoning: str
    opi_applied: str
    confidence: float

class PositionResult(BaseModel):
    section: str
    group: str
    # --- SGR: пошаговый бланк ОПИ ---
    step1_product_nature: str          # «Материал, форма, обработка»
    step2_opi1_text_analysis: str      # «Какие тексты позиций точно описывают товар»
    step3_ambiguity_resolution: str    # «Применение ОПИ 2/3 или "Однозначно по ОПИ 1"»
    # --- Решение ПОСЛЕ анализа ---
    candidates: list[PositionCandidate]
    selected_position: str
    ...
```

**Зачем**: Это уровень, где допускаются самые «дорогие» ошибки (неверная позиция = неверный код). Три пошаговых поля воспроизводят логику эксперта:

| Шаг | Поле | Что делает эксперт |
|:---:|------|-------------------|
| 1 | `step1_product_nature` | Определяет, **что** перед ним (материал, форма, обработка) |
| 2 | `step2_opi1_text_analysis` | Сопоставляет с **текстами** позиций (ОПИ 1) |
| 3 | `step3_ambiguity_resolution` | Если неоднозначно — применяет **ОПИ 2/3** |

`text_match_score` в кандидатах (`exact/partial/weak`) — это оценка соответствия, которая заставляет модель быть объективной: если текст позиции лишь «weak» соответствует товару, confidence должен быть низким.

#### L4 — Субпозиция (`subposition.py`)

```python
class SubpositionResult(BaseModel):
    position: str
    # --- SGR: работа с порогами ---
    parameter_extraction: str   # «Масса: 720 г, мощность: не указана»
    threshold_analysis: str     # «720 г > 250 г → субпозиция 8806.22»
    # --- Решение ---
    candidates: list[SubpositionCandidate]
    selected_subposition: str
    ...
```

**Зачем**: Уровень L4 — это часто выбор по **числовым порогам** (масса, мощность, объём). В v6/v7 flash-lite ошибалась именно здесь: дрон 720 г классифицировался как «≤250 г» (P3). SGR-поля заставляют модель:
1. Сначала **извлечь** параметры из карточки
2. Затем **найти** пороги в текстах субпозиций
3. **Сопоставить** и только потом выбрать

#### L5 — Финальный 10-значный код (`subsubposition.py`)

```python
class SubsubpositionResult(BaseModel):
    subposition: str
    # --- SGR: анализ перед выбором ---
    relevant_parameters: str    # «Какие параметры определяют выбор»
    code_differentiation: str   # «Чем различаются коды»
    threshold_comparison: str   # «Параметр X = Y, порог = Z, Y > Z → код A»
    # --- Решение ---
    selected_code_8: str
    selected_code_10: str
    reasoning: str
    confidence: float
    ...
```

**Зачем**: Аналогично L4, но на уровне 10-значных кодов. Дополнительно `code_differentiation` заставляет модель понять *чем* коды отличаются (иногда это тонкие различия типа «для промышленного использования» vs «прочие»), прежде чем выбирать.

### 3.3 Per-level thinking budget

#### Проблема

В v8 `thinkingBudget=1024` был зашит для **всех** уровней. Это неоптимально:
- L0 (обогащение) и L5 (финальный код) — простые задачи, thinking не нужен
- L3 (позиция) — сложная задача, thinking полезен
- С SGR часть работы thinking перенесена в структуру схемы

#### Решение

Добавлен маппинг `LEVEL_THINKING_BUDGET` в `src/feap/llm/models.py`:

```python
LEVEL_THINKING_BUDGET = {
    0: 0,      # L0 обогащение — SGR достаточно
    1: 256,    # L1 раздел — лёгкий анализ
    2: 256,    # L2 группа — лёгкий
    3: 512,    # L3 позиция — самый сложный, SGR + thinking
    4: 256,    # L4 субпозиция — пороги, SGR помогает
    5: 0,      # L5 финальный код — SGR достаточно
    99: 0,     # NTM верификация
}
```

#### Конфигурация

Переменная окружения `GEMINI_THINKING_BUDGET` в `src/feap/config.py`:

| Значение | Поведение |
|----------|----------|
| `-1` (по умолчанию) | Используются per-level значения из таблицы выше |
| `0` | Thinking отключён для всех уровней (для тестирования SGR-only) |
| `1024` | Поведение v8 (thinking 1024 для всех уровней) |
| Любое `>=0` | Используется как глобальный override для всех уровней |

#### Реализация

В `src/feap/llm/gemini_provider.py`:

```python
thinking_budget = get_thinking_budget(level)
config = types.GenerateContentConfig(
    ...
    thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget),
)
```

### 3.4 Промпты — секция «ПОРЯДОК АНАЛИЗА»

Каждый system-промпт получил секцию, инструктирующую модель заполнять SGR-поля **строго по порядку**. Промпт **не дублирует** описания полей из схемы, а даёт **экспертные указания**, что именно анализировать.

Пример (L3b deep, самый критичный):

```
ПОРЯДОК АНАЛИЗА (заполняй поля СТРОГО ПО ПОРЯДКУ):
1. step1_product_nature — опиши ЧТО это: материал, физическая форма, степень обработки. НЕ назначение.
2. step2_opi1_text_analysis — для каждой позиции-кандидата: точно ли её текст описывает товар?
   Цитируй ключевые фрагменты текстов позиций.
3. step3_ambiguity_resolution — если несколько позиций подходят: примени ОПИ 2а (некомплектные),
   3а (конкретность), 3б (основной характер). Если однозначно — напиши «Однозначно по ОПИ 1».
4. candidates — заполни на основе проведённого анализа, text_match_score для каждого.
5. selected_position — выбери ПОСЛЕ всех шагов анализа.
```

---

## 4. Оценка влияния на токены

### 4.1 SGR overhead (output tokens)

SGR-поля добавляют ~150–300 output-токенов на один LLM-вызов (6–8 полей × 20–40 слов каждое). На 8 вызовов каскада: ~1,200–2,400 дополнительных output-токенов.

| Модель | Output $/MTok | SGR overhead/классификация |
|--------|:---:|:---:|
| flash-lite | $0.40 (или $1.50 для 3.1) | $0.0005–$0.001 |
| 2.5-flash | $2.50 | $0.003–$0.006 |

### 4.2 Thinking economy (thinking tokens)

С SGR можно снизить thinking budget, т.к. часть рассуждений вынесена в структуру:

| Конфигурация | Thinking tok/вызов | Thinking tok/классификация (8 вызовов) | Thinking cost |
|:---:|:---:|:---:|:---:|
| v8 (thinkingBudget=1024) | ~800 | ~6,400 | $0.016 |
| SGR + per-level | 0–512 | ~1,536 | **$0.004** |
| SGR + no thinking | 0 | 0 | **$0.000** |

### 4.3 Общий баланс

| Конфигурация | Input | Output + SGR | Thinking | **Итого** | vs v8 |
|:---:|:---:|:---:|:---:|:---:|:---:|
| v8 baseline | $0.011 | $0.006 | $0.016 | **$0.033** | — |
| SGR + per-level thinking | $0.011 | $0.009 | $0.004 | **~$0.024** | **-27%** |
| SGR + no thinking | $0.011 | $0.009 | $0.000 | **~$0.020** | **-39%** |
| flash-lite + SGR | $0.003 | $0.001 | $0.000 | **~$0.004** | **-88%** |

> Цены: 2.5-flash = $0.30/$2.50/$2.50 (input/output/thinking), flash-lite-preview = $0.25/$1.50.

---

## 5. Конфигурации для бенчмарка

### 5.1 Матрица тестирования

| # | Конфигурация | Модель | SGR | ThinkingBudget | Ожидание |
|:-:|-------------|--------|:---:|:--------------:|----------|
| A | flash-lite + SGR | gemini-3.1-flash-lite-preview | Да | N/A (нет thinking) | 65–72% code-10 (vs 62% без SGR) |
| B | 2.5-flash + SGR + no thinking | gemini-2.5-flash | Да | 0 | ~70% при $0.020 |
| C | 2.5-flash + SGR + per-level | gemini-2.5-flash | Да | -1 (0/256/512) | **75%+ при $0.024** |
| D | 2.5-flash baseline (v8) | gemini-2.5-flash | Нет | 1024 | 73% при $0.033 |

### 5.2 Как запустить

```bash
# Конфигурация A: flash-lite + SGR
export LLM_PROVIDER=google
export GEMINI_MODEL_LIGHT=gemini-3.1-flash-lite-preview
export GEMINI_MODEL_MEDIUM=gemini-3.1-flash-lite-preview
export GEMINI_MODEL_HEAVY=gemini-3.1-flash-lite-preview
export GEMINI_THINKING_BUDGET=0

# Конфигурация B: 2.5-flash + SGR + no thinking
export GEMINI_MODEL_LIGHT=gemini-2.5-flash
export GEMINI_MODEL_MEDIUM=gemini-2.5-flash
export GEMINI_MODEL_HEAVY=gemini-2.5-flash
export GEMINI_THINKING_BUDGET=0

# Конфигурация C: 2.5-flash + SGR + per-level thinking
export GEMINI_MODEL_LIGHT=gemini-2.5-flash
export GEMINI_MODEL_MEDIUM=gemini-2.5-flash
export GEMINI_MODEL_HEAVY=gemini-2.5-flash
export GEMINI_THINKING_BUDGET=-1

# Конфигурация D: 2.5-flash baseline v8 (без SGR — нужен откат схем)
export GEMINI_THINKING_BUDGET=1024
```

Запуск бенчмарка:
```bash
python benchmark/scripts/evaluate_http.py
```

---

## 6. Изменённые файлы

### Схемы

| Файл | SGR-поля |
|------|----------|
| `src/feap/schemas/product_card.py` | `product_identification`, `classification_relevant_features` |
| `src/feap/schemas/section.py` | Shortlist: `product_nature`, `primary_section_signal`; Candidate: `text_match_analysis`; Result: `key_notes_analysis` |
| `src/feap/schemas/group.py` | Candidate: `text_match_analysis`; Result: `product_in_section_context`, `key_exclusion_notes` |
| `src/feap/schemas/position.py` | Shortlist: `physical_nature_analysis`, `key_differentiators`; Candidate: `text_match_score`; Result: `step1_product_nature`, `step2_opi1_text_analysis`, `step3_ambiguity_resolution` |
| `src/feap/schemas/subposition.py` | `parameter_extraction`, `threshold_analysis` |
| `src/feap/schemas/subsubposition.py` | `relevant_parameters`, `code_differentiation`, `threshold_comparison` |

### Промпты

| Файл | Добавлено |
|------|-----------|
| `src/feap/prompts/templates/system/level0_enrichment.jinja2` | Секция «ПОРЯДОК ЗАПОЛНЕНИЯ» |
| `src/feap/prompts/templates/system/level1_shortlist.jinja2` | Секция «ПОРЯДОК АНАЛИЗА» |
| `src/feap/prompts/templates/system/level1_section.jinja2` | Секция «ПОРЯДОК АНАЛИЗА» |
| `src/feap/prompts/templates/system/level2_group.jinja2` | Секция «ПОРЯДОК АНАЛИЗА» |
| `src/feap/prompts/templates/system/level3_shortlist.jinja2` | Секция «ПОРЯДОК АНАЛИЗА» |
| `src/feap/prompts/templates/system/level3_deep.jinja2` | Секция «ПОРЯДОК АНАЛИЗА» (ОПИ пошагово) |
| `src/feap/prompts/templates/system/level4_subposition.jinja2` | Секция «ПОРЯДОК АНАЛИЗА» (пороги) |
| `src/feap/prompts/templates/system/level5_subsubposition.jinja2` | Секция «ПОРЯДОК АНАЛИЗА» (пороги) |

### Инфраструктура

| Файл | Изменение |
|------|-----------|
| `src/feap/llm/models.py` | `LEVEL_THINKING_BUDGET`, `get_thinking_budget()` |
| `src/feap/llm/gemini_provider.py` | Per-level thinking budget вместо hardcoded 1024 |
| `src/feap/config.py` | `GEMINI_THINKING_BUDGET: int = -1` |

### Не изменены

Level handlers (`level0.py`–`level5.py`), оркестратор, backtracker, context-шаблоны, API-схемы — всё без изменений. SGR-поля заполняются моделью, но не используются в бизнес-логике.

---

## 7. Принципы дизайна

### 7.1 SGR-поля — это «scaffolding», не бизнес-данные

SGR-поля существуют **только для направления рассуждений модели**. Они:
- Заполняются LLM при генерации JSON
- Влияют на качество последующих полей (constrained decoding)
- **Не читаются** кодом level handlers
- **Не сохраняются** в classification_chain

Это позволяет менять SGR-поля без риска сломать бизнес-логику.

### 7.2 Порядок полей = порядок мышления

В JSON Schema (и constrained decoding) поля генерируются в порядке определения. Поэтому:

```
step1_product_nature → step2_opi1_text_analysis → step3_ambiguity_resolution → selected_position
```

Модель **физически не может** заполнить `selected_position` не заполнив предыдущие шаги. Это и есть ключевой механизм SGR.

### 7.3 Имена полей как attention signals

SGR использует **осмысленные имена** полей: `threshold_comparison`, `physical_nature_analysis`, `exclusion_check`. При constrained decoding имя поля копируется в контекст генерации и получает повышенное внимание модели. Поле `threshold_comparison` буквально фокусирует модель на сравнении порогов.

### 7.4 defaults для backward compatibility

Все SGR-поля имеют `default=""`. Это критично:
- Gemini API **отклоняет** `default` в схеме (они удаляются `_strip_defaults()`)
- В Python-коде `default=""` позволяет создавать объекты без SGR-полей (в тестах, в auto-select логике L4/L5)
- Если модель по какой-то причине не заполнит SGR-поле, валидация не упадёт

---

## 8. Связь с предыдущими проблемами

### P3 (v7): Числовые пороги игнорируются

**Проблема**: Дрон 720 г → «≤250 г». flash-lite не следовала инструкции «сопоставь числа».

**Решение SGR**: Поля `parameter_extraction` и `threshold_analysis` в L4/L5 **заставляют** модель явно извлечь числа и сравнить с порогами — это не инструкция в тексте, которую можно проигнорировать, а поле, которое нужно заполнить.

### P5 (v7): Назначение вместо физической природы

**Проблема**: «Профиль для оконных рам» → оконная рама, а не профиль.

**Решение SGR**: Поля `physical_nature_analysis` (L3a) и `step1_product_nature` (L3b) с инструкцией «НЕ назначение» — модель должна заполнить описание физической природы **до** выбора позиции.

### Ложные needs_input (v6/v7)

**Проблема**: flash-lite выдавала 15 ложных `needs_input` из 100 кейсов.

**Решение SGR**: SGR направляет модель через анализ к решению, а не к сомнению. Поля заканчиваются `selected_*`, что даёт сигнал «ты должен выбрать».

---

## 9. Дальнейшие направления

### 9.1 Адаптивные SGR-поля

Текущие SGR-поля одинаковы для всех товаров. Возможная оптимизация — динамические SGR-поля в зависимости от типа товара (составной → добавить поле `dominant_component_analysis`; с числовыми порогами → добавить `numeric_threshold_table`).

### 9.2 SGR для Anthropic

Текущая реализация оптимизирована под Gemini (constrained JSON decoding). Для Anthropic (tool_use) механизм аналогичен — tool parameters тоже генерируются последовательно. Нужно проверить, что `_strip_defaults()` не конфликтует с Anthropic tool schema.

### 9.3 Гибрид моделей + SGR

Наиболее перспективная конфигурация — flash-lite для простых уровней (L0, L1, L5) и 2.5-flash для сложных (L3b), оба с SGR. SGR компенсирует слабости flash-lite на простых уровнях, а thinking 2.5-flash дополняет SGR на сложных.

### 9.4 Метрики SGR-полей

После бенчмарка можно анализировать содержимое SGR-полей:
- Коррелирует ли `text_match_score: "exact"` с правильным ответом?
- Заполняет ли flash-lite `threshold_comparison` корректно?
- Можно ли по SGR-полям предсказать вероятность ошибки?
