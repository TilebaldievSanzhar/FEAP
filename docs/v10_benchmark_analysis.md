# Benchmark v10: SGR L4-L5 Enhancement — Analysis & Regression Fix

**Дата:** 2026-03-23
**Статус:** Исправлено, ожидает повторного бенчмарка (v10b)

---

## 1. Что было сделано (v10)

### Цель
Улучшить accuracy на уровнях L4 (субпозиция) и L5 (подсубпозиция), которые стали bottleneck после SGR v9. В v9: heading=99%, code-10=77%, все 22 ошибки — на L4-L5.

### Изменения в схемах

**`subposition.py` (L4)** — 3 новых SGR-поля + 1 в кандидате:
- `hierarchy_parsing` (шаг 0) — разбор основных категорий/веток субпозиций
- `product_card_parameter_check` (шаг 1) — дословное цитирование параметров из карточки
- `selection_consistency_check` (после candidates, перед selected) — проверка согласованности
- `category_match` в `SubpositionCandidate` — `direct`/`by_parameter`/`residual`

**`subsubposition.py` (L5)** — 1 новое SGR-поле:
- `parent_context` (шаг 0) — понимание контекста текущей субпозиции

### Изменения в промптах

**`level4_subposition.jinja2`** — полная переработка:
- ПОРЯДОК АНАЛИЗА расширен с 4 до 7 шагов (0-6)
- Новая секция **КРИТИЧЕСКИЕ ОШИБКИ** — запреты на 5 типичных ошибок (не путать целое с частями, не выбирать «прочие» при наличии конкретной субпозиции, не придумывать параметры, и т.д.)
- Инструкция про разделитель "🠺" для понимания иерархии

**`level5_subsubposition.jinja2`** — добавлен шаг 0 (`parent_context`)

### Изменения в данных/шаблонах (ВЫЗВАЛИ РЕГРЕССИЮ)

**`tree_nodes.jinja2`** — добавлена логика отображения `parent_category` с `{% if %}` блоками
**`level4.py`** — добавлено извлечение `parent_category` из имён с "🠺"

---

## 2. Результаты v10 (регрессия!)

| Метрика | v9 | v10 | Δ |
|---------|:--:|:---:|:-:|
| Heading accuracy | **99%** | 97% | **-2** |
| Subheading accuracy | **84%** | 78% | **-6** |
| Code-10 accuracy | **77%** | 69% | **-8** |
| Section accuracy | **99%** | 97% | **-2** |
| Hierarchical score | **0.9105** | 0.8675 | **-0.043** |
| Completed | **99** | 98 | -1 |
| Errors | **1** | 2 | +1 |

### Сравнение кейсов

- **Регрессии (v9 OK → v10 ошибка): 10 кейсов**
  - E18: section error (NEW!)
  - H01: section error (NEW!)
  - E23: subheading
  - H09, H15, M05, M14, M16, M17: subheading
  - H26: code_8

- **Улучшения (v9 ошибка → v10 OK): 2 кейса**
  - H21 (power station LiFePO4): was subheading error → OK!
  - M19 (leather wet-blue): was subheading error → OK!

---

## 3. Причина регрессии

### Корневая причина: `tree_nodes.jinja2`

Шаблон `tree_nodes.jinja2` используется на **ВСЕХ** уровнях каскада (L1-L5). Изменение этого шаблона с добавлением `{% if %}` блоков:

```jinja2
{% if node.get("parent_category") %}- **{{ node.code }}** — [{{ node.parent_category }}] > ...
{% else %}- **{{ node.code }}** — {{ node.name }}
{% endif %}
```

Вызвало **лишние пустые строки** между элементами списка из-за whitespace в Jinja2. Это повлияло на все уровни, включая L1 (разделы), вызвав 2 новые section-level ошибки.

### Дополнительная причина: перегрузка SGR-полями

Flash-lite — лёгкая модель с ограниченной генеративной способностью. Добавление 5+ новых SGR-полей (hierarchy_parsing, product_card_parameter_check, selection_consistency_check, category_match per candidate, parent_context) может перегружать модель, отнимая "бюджет внимания" у основных decision-полей.

Это подтверждается тем, что даже после отката tree_nodes (v10b) часть subheading-ошибок могла остаться — модель тратит слишком много output-токенов на аналитические поля.

---

## 4. Что исправлено для v10b

### Откачено:
1. **`tree_nodes.jinja2`** — возвращён к оригинальной версии (без `{% if %}`)
2. **`level4.py`** — убрано извлечение `parent_category`

### Оставлено (SGR-поля в схемах и промпты):
Все изменения в `subposition.py`, `subsubposition.py`, `level4_subposition.jinja2`, `level5_subsubposition.jinja2` — оставлены. Это основные SGR-улучшения, которые дали 2 улучшения (H21, M19).

---

## 5. Ожидания от v10b

С откатом tree_nodes section-level ошибки (E18, H01) должны исчезнуть. Вопрос — останутся ли subheading-регрессии из-за перегрузки SGR-полями.

**Возможные сценарии:**
1. **Оптимистичный**: section ошибки уходят, SGR-поля дают net-positive → code-10 ≥ 77%
2. **Нейтральный**: section ошибки уходят, но SGR overhead компенсирует улучшения → code-10 ≈ 77%
3. **Пессимистичный**: SGR-поля слишком тяжелы для flash-lite → code-10 < 77%

### Если v10b покажет регрессию на subheading

Нужно **сократить** SGR-поля до минимума:
- Оставить только `hierarchy_parsing` и `category_match` (самые импактные)
- Убрать `product_card_parameter_check` (можно заменить инструкцией в промпте)
- Убрать `selection_consistency_check` (overhead > benefit для flash-lite)
- Убрать `parent_context` из L5

Или переключить L4 на более мощную модель (gemini-2.5-flash) — SGR + thinking на сложных уровнях, flash-lite на простых.

---

## 6. Паттерны ошибок v9 (для справки)

Из анализа 10 ключевых ошибочных кейсов v9:

| Паттерн | Кейсы | Описание |
|---------|-------|----------|
| Reasoning ≠ Selection | H20, H21 | Модель пишет правильный анализ, но выбирает противоположный код |
| Навигация по дереву | H02, H27 | Выбирает «части» вместо целого устройства |
| Галлюцинация параметров | M09, H14 | Придумывает числовые значения вместо извлечения из карточки |
| Неверная ветка | H18, H19, M22 | Правильный параметр, но неверная категория |
| Не сработал needs_input | M19 | Модель пишет «нужны данные», но не заполняет missing_parameters |

### Подтверждённые улучшения v10

- **H21** (power station LiFePO4): v9 выбирала `850790` (части), v10 — `8507600000` (литий-ионные аккумуляторы) ✓
- **M19** (кожа wet-blue): v9 выбирала `410419` (прочие), v10 — `4104115100` (лицевая недвоёная) ✓

Оба кейса — именно те паттерны, на которые были нацелены SGR-улучшения (hierarchy_parsing для H21, product_card_parameter_check для M19).

---

## 7. Файлы изменений

### Текущее состояние (после фикса для v10b):

| Файл | Статус |
|------|--------|
| `src/feap/schemas/subposition.py` | Изменён (SGR-поля) |
| `src/feap/schemas/subsubposition.py` | Изменён (parent_context) |
| `src/feap/prompts/templates/system/level4_subposition.jinja2` | Изменён (полная переработка) |
| `src/feap/prompts/templates/system/level5_subsubposition.jinja2` | Изменён (parent_context step) |
| `src/feap/prompts/templates/context/tree_nodes.jinja2` | **Откачен** к оригиналу |
| `src/feap/cascade/levels/level4.py` | **Откачен** к оригиналу |

### Результаты бенчмарков

| Файл | Описание |
|------|----------|
| `benchmark/data/results/v9_sgr_flashlite/` | Baseline (heading=99%, code-10=77%) |
| `benchmark/data/results/v10_sgr_l4l5_enhanced/` | Регрессия (heading=97%, code-10=69%) |
| `benchmark/data/results/v10b_sgr_l4l5_fixed/` | Ожидает прогона |

---

## 8. Команда для запуска v10b бенчмарка

```bash
cd C:/Projects/FEAP
# Убедиться что контейнер пересобран с последними изменениями:
docker compose up -d --build app
# Подождать 3-5 сек и проверить health:
curl http://localhost:8000/health
# Запустить бенчмарк через venv:
PYTHONUTF8=1 venv/Scripts/python.exe benchmark/scripts/evaluate_http.py \
  --dataset benchmark/data/cases/benchmark_100.jsonl \
  --run-id v10b_sgr_l4l5_fixed \
  --api-url http://localhost:8000 \
  --results-dir benchmark/data/results/v10b_sgr_l4l5_fixed \
  --timeout 180 \
  --no-resume
```

После завершения — сравнить с v9:
```bash
PYTHONUTF8=1 venv/Scripts/python.exe -c "
import json
v9, v10b = {}, {}
for path, d in [('benchmark/data/results/v9_sgr_flashlite/v9_sgr_flashlite_case_results.jsonl', v9),
                ('benchmark/data/results/v10b_sgr_l4l5_fixed/v10b_sgr_l4l5_fixed_case_results.jsonl', v10b)]:
    with open(path, encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            d[r['case_id']] = r
regressions = [(c, v10b[c]['first_error_level']) for c in v9 if v9[c]['code_10_match'] and not v10b.get(c,{}).get('code_10_match',False)]
improvements = [(c, v9[c]['first_error_level']) for c in v9 if not v9[c]['code_10_match'] and v10b.get(c,{}).get('code_10_match',False)]
print(f'Regressions: {len(regressions)}')
for c, e in sorted(regressions): print(f'  {c}: {e}')
print(f'Improvements: {len(improvements)}')
for c, e in sorted(improvements): print(f'  {c}: was {e}')
"
```
