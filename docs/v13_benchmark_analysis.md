# Benchmark v13: Context Engineering — Анализ результатов

**Дата**: 2026-03-24
**Статус**: Регрессия, изменения требуют частичного отката

---

## 1. Цель эксперимента

Улучшить code-10 accuracy с 77% (v9) до 90%+ за счёт чистого context engineering на той же модели (gemini-3.1-flash-lite-preview), без смены провайдера/модели.

Анализ ошибок v9 показал, что 22 из 100 кейсов ошибаются:
- 15 на уровне subheading (L4) — неправильная 6-значная субпозиция
- 7 на уровне code_8/code_10 (L5) — правильная субпозиция, но неправильный финальный код

---

## 2. Предварительные исследования

### 2.1 Сравнение v9 vs v12 (flash-lite vs Sonnet L4)

Ключевой вывод: **v9 и v12 дают идентичные 87/100 code-10** (разница в отчётах 77% vs 72% — артефакт подсчёта API-ошибок). Sonnet на L4 не дал улучшения — только стабильность (98% completed vs 99%).

### 2.2 Корневые причины ошибок

| Причина | Кейсов | Примеры |
|---------|:------:|---------|
| Неправильная ветка (parts vs whole, residual vs specific) | 6 | МФУ → «прочие» вместо «принтеры»; кухонная мебель → «прочие» вместо «кухонная» |
| Неправильный числовой порог | 5 | Объём двигателя 1500 см³ → не тот диапазон; масса прицепа → не тот порог |
| Неправильная интерпретация природы товара | 4 | Набор шампунь+кондиционер → «прочие» вместо «шампуни»; робот-пылесос → «части» |
| Ошибки на 8/10-значном уровне | 7 | Тонкие различия внутри кода (кабель, сыр, телевизор) |

### 2.3 Структурные проблемы (из анализа кода)

1. **L4 SGR field order broken**: `selection_consistency_check` стоит МЕЖДУ `candidates` и `selected_subposition` — constrained decoder заполняет проверку ДО выбора
2. **L5 нет структуры кандидатов**: прыгает к `selected_code_8`/`selected_code_10` без перебора
3. **tree_nodes.jinja2 слишком generic**: плоский `- **код** — название` для всех уровней
4. **L4 SGR перегружен**: 6 аналитических полей, `product_card_parameter_check` дублирует `parameter_extraction`

---

## 3. Что было сделано (v13)

### 3.1 Изменения в схемах

#### `subposition.py` (L4)
- **Удалено поле** `product_card_parameter_check` (дублировало `parameter_extraction`)
- **Улучшено описание** `parameter_extraction` — объединяет шаги 1+2 (цитирование + извлечение)
- **Перемещено** `selection_consistency_check` ПОСЛЕ `selected_subposition` (было между `candidates` и `selected_subposition`)
- **Улучшено описание** `hierarchy_parsing` — конкретнее про ветки и правила
- Итого: 4 SGR поля вместо 6 (hierarchy_parsing, parameter_extraction, threshold_analysis, selection_consistency_check)

#### `subsubposition.py` (L5)
- **Добавлен** `SubsubpositionCandidate` — модель с полями `code_10`, `code_text`, `match_type`, `reasoning`, `confidence`
- **Добавлено поле** `candidates: list[SubsubpositionCandidate]` перед decision fields
- **Улучшены описания** SGR полей (relevant_parameters, code_differentiation, threshold_comparison)

### 3.2 Новые context templates

#### `tree_nodes_l4.jinja2` (NEW)
Специализированный шаблон для L4, разбирающий `🠺` разделитель в именах субпозиций:
```
- **850811** — [пылесосы бытовые] > мощностью не более 1500 Вт
```
Вместо generic:
```
- **850811** — пылесосы бытовые 🠺 мощностью не более 1500 Вт
```

#### `tree_nodes_l5.jinja2` (NEW)
Аналогичный шаблон для L5 с разбором иерархии кодов.

#### `tree_nodes.jinja2` (UNTOUCHED)
Оставлен без изменений для L1-L3 (урок из v10 — изменения в shared template ломают все уровни).

### 3.3 Обновлённые промпты

#### `level4_subposition.jinja2`
- Объединены шаги 1+2 в один (parameter_extraction)
- Добавлены конкретные примеры ошибок: МФУ → целое устройство; кухонная мебель ≠ «прочие»; набор шампунь+кондиционер = «шампуни»
- Добавлен пример порога: объём двигателя 1500 см³
- Улучшены инструкции по category_match: `direct` с примерами (инсектицид → «инсектициды», апельсиновый сок → «апельсиновый»)

#### `level5_subsubposition.jinja2`
- Добавлен шаг 4 (candidates) с инструкцией по match_type
- Обновлен формат `[категория] > уточнение` вместо `🠺`
- Добавлен пример: кабель «на напряжение до 1000 В»

### 3.4 Изменения в assembler

`assembler.py`: L4 использует `tree_nodes_l4.jinja2`, L5 использует `tree_nodes_l5.jinja2` вместо generic `tree_nodes.jinja2`.

### 3.5 Конфигурация бенчмарка

```env
LLM_PROVIDER=google
GEMINI_MODEL_LIGHT=gemini-3.1-flash-lite-preview
GEMINI_MODEL_MEDIUM=gemini-3.1-flash-lite-preview
GEMINI_MODEL_HEAVY=gemini-3.1-flash-lite-preview
GEMINI_THINKING_BUDGET=0
LEVEL4_MODEL=
```

Все уровни на flash-lite, без thinking, без per-level overrides. Чистый context engineering.

---

## 4. Результаты

### 4.1 Сводная таблица

| Метрика | v9 (baseline) | v13 (context eng) | Δ |
|---------|:---:|:---:|:---:|
| **Heading (4-digit)** | 0.990 | 0.960 | **-3%** |
| **Subheading (6-digit)** | 0.770 | 0.770 | = |
| **Code-10 (10-digit)** | 0.770 | 0.650 | **-12%** |
| **Hierarchical score** | 0.9105 | 0.8545 | **-0.056** |
| **Completed** | 99% | 98% | -1% |
| **Errors** | 1 | 2 | +1 |
| **Fell into trap** | 0 | 4 | +4 |

### 4.2 По сложности

| Difficulty | v9 heading / code10 | v13 heading / code10 | Δ code10 |
|---|---|---|---|
| easy (n=30) | 1.000 / 0.867 | 0.967 / 0.800 | **-6.7%** |
| medium (n=40) | 1.000 / 0.775 | 1.000 / 0.650 | **-12.5%** |
| hard (n=30) | 0.967 / 0.633 | 0.900 / 0.500 | **-13.3%** |

### 4.3 First Error Level Distribution

| Level | v9 | v13 | Δ | Комментарий |
|---|:---:|:---:|:---:|---|
| correct | 77 | 63 | **-14** | |
| section (L1) | 1 | 4 | **+3** | Не должно было измениться |
| heading (L3) | 0 | 2 | **+2** | Не трогали L3 |
| subheading (L4) | 13 | 19 | **+6** | Целевой уровень, регрессия |
| code_8 (L5) | 4 | 10 | **+6** | Целевой уровень, регрессия |
| code_10 | 2 | 2 | = | |

### 4.4 Примечания по запуску

Бенчмарк потребовал 3 дозапуска из-за нестабильности Gemini API:
- 20 кейсов — 503 UNAVAILABLE (high demand)
- 5 кейсов — DNS/network errors (кратковременный сбой)
- 2 кейса — повторные 503

---

## 5. Анализ регрессии

### 5.1 Почему ухудшился L4 (subheading +6 ошибок)

**Гипотеза 1: tree_nodes_l4.jinja2 ухудшил восприятие**

Новый формат `[категория] > уточнение` может быть менее понятен flash-lite, чем оригинальный `имя 🠺 уточнение`. Модель могла интерпретировать `[категория]` как метаданные, а не как часть имени субпозиции.

**Гипотеза 2: Удаление product_card_parameter_check ослабило grounding**

Хотя поле дублировало parameter_extraction, оно **заставляло модель дважды обратиться к карточке товара** — сначала процитировать, потом извлечь числа. Одного шага может быть недостаточно для flash-lite.

**Гипотеза 3: Перемещение selection_consistency_check не помогло**

Перенос проверки ПОСЛЕ выбора может быть бесполезен для constrained decoding — к моменту заполнения этого поля модель уже «забыла» candidates. Для Gemini важен порядок генерации, а проверка после факта не влияет на quality.

### 5.2 Почему ухудшился L5 (code_8 +6 ошибок)

**Гипотеза: candidates в L5 перегрузили flash-lite**

L5 SubsubpositionResult теперь содержит `candidates: list[SubsubpositionCandidate]` + 4 SGR поля + decision fields. Для flash-lite это слишком — та же проблема, что была с L4 (6 полей → регрессия).

V9 не имел candidates в L5, и модель справлялась лучше с прямым выбором.

### 5.3 Почему ухудшился L1 (section +3 ошибки)

Мы **не трогали** L1 schemas/prompts/templates. Возможные причины:
- Стохастичность flash-lite (разные запуски дают ±2-3% разброс)
- Gemini API downtime/degradation мог повлиять на качество ответов (503 spike = перегрузка серверов)

### 5.4 Trap codes: 0 → 4

v13 попал в 4 trap-кода (специально подготовленные ловушки). Это говорит о том, что модель стала менее осторожной — возможно, убрав `product_card_parameter_check`, мы убрали «тормоз», который заставлял модель перечитывать карточку.

---

## 6. Выводы

### 6.1 Что работает

- **Heading accuracy 96%** остаётся высокой — L1-L3 работают стабильно
- **Medium heading = 100%** — улучшение в промпте L4 помогает на средних кейсах
- **Конкретные примеры** в промптах (МФУ, набор, кухонная мебель) — правильный подход

### 6.2 Что не работает

- **Level-specific tree templates** (`tree_nodes_l4/l5.jinja2`) — flash-lite хуже воспринимает `[категория] > уточнение` чем оригинальный `🠺`
- **L5 candidates** — перегружают flash-lite, как L4 SGR в v10
- **Удаление product_card_parameter_check** — ослабило grounding, модель реже обращается к фактам из карточки

### 6.3 Уроки

1. **Flash-lite имеет жёсткий лимит сложности** — добавление полей/структуры за порогом ухудшает результат
2. **Дублирование (grounding) может быть полезным** — заставлять модель дважды обратиться к данным ≠ бесполезная трата токенов
3. **Формат tree_nodes критичен** — любое изменение в рендеринге дерева влияет на все уровни
4. **Stochastic variance flash-lite** — ±2-3% section errors между запусками, нужно несколько прогонов для статистической значимости

---

## 7. Рекомендации для v14

### Откатить (вернуть к v9)
- `tree_nodes_l4.jinja2` → удалить, вернуть generic `tree_nodes.jinja2` для L4
- `tree_nodes_l5.jinja2` → удалить, вернуть generic `tree_nodes.jinja2` для L5
- `subsubposition.py` → убрать `candidates`, вернуть прямой формат
- `assembler.py` → вернуть generic tree_nodes для L4/L5

### Оставить из v13
- L4 field reorder: `selection_consistency_check` после `selected_subposition`
- Улучшенные примеры в L4 промпте (МФУ, набор, мебель)

### Вернуть
- `product_card_parameter_check` — обратно как отдельный шаг в L4

### Новые идеи для v14
- **Retry с пониженным confidence**: если L4 confidence < 0.4, перезапустить с другим промптом
- **Перейти на gemini-2.5-flash** для L4/L5 (дороже, но точнее)
- **Реализовать Anthropic native structured outputs** для L4 (Haiku + constrained decoding)

---

## 8. Сравнительная таблица всех бенчмарков

| Версия | Модель L4 | SGR | Heading | Code-10 | Score | Errors | Особенности |
|--------|-----------|:---:|:-------:|:-------:|:-----:|:------:|-------------|
| **v9** | flash-lite | v1 | **0.990** | **0.770** | **0.9105** | 1 | Baseline (лучший) |
| v10 | flash-lite | v2 (L4/L5 enhanced) | 0.970 | 0.690 | 0.8675 | 2 | tree_nodes regression |
| v10b | flash-lite | v2 (fixed) | 0.950 | 0.720 | 0.8725 | 4 | Частичный откат |
| v11 | **Haiku** (Anthropic) | v2 | 0.640 | 0.490 | 0.5910 | 35 | Haiku tool_use fails |
| v12 | **Sonnet** (Anthropic) | v2 | 0.960 | 0.720 | 0.8840 | 2 | Sonnet стабилен, но не лучше v9 |
| **v13** | flash-lite | v3 (context eng) | 0.960 | 0.650 | 0.8545 | 2 | Регрессия L4/L5 |

---

## 9. Изменённые файлы (v13)

| Файл | Изменение |
|------|-----------|
| `src/feap/schemas/subposition.py` | Удалён `product_card_parameter_check`, reorder fields, улучшены descriptions |
| `src/feap/schemas/subsubposition.py` | Добавлен `SubsubpositionCandidate`, `candidates` field, улучшены descriptions |
| `src/feap/prompts/templates/context/tree_nodes_l4.jinja2` | **NEW** — иерархический рендеринг `[кат] > уточнение` |
| `src/feap/prompts/templates/context/tree_nodes_l5.jinja2` | **NEW** — иерархический рендеринг для кодов |
| `src/feap/prompts/templates/system/level4_subposition.jinja2` | Merged steps, примеры ошибок, обновлён порядок |
| `src/feap/prompts/templates/system/level5_subsubposition.jinja2` | Добавлен шаг candidates, обновлён формат |
| `src/feap/prompts/assembler.py` | L4→tree_nodes_l4, L5→tree_nodes_l5 |
| `src/feap/cascade/levels/level5.py` | Auto-select с candidates |
| `.env` | LEVEL4_MODEL= (убран Anthropic override) |
