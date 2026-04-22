# FEAP — Foreign Economic Activity Platform

Каскадный LLM-классификатор товаров по **ТН ВЭД ЕАЭС** (10-значные коды
таможенного тарифа) с расчётом сопутствующих платежей и проверкой мер
нетарифного регулирования (НТР) для Кыргызской Республики.

Система последовательно спускается по дереву номенклатуры
(раздел → группа → позиция → субпозиция → подсубпозиция), на каждом шаге
вызывая LLM с релевантным контекстом: примечаниями, пояснениями, ОПИ.
Сложные уровни (L1, L3, L5) реализованы в две фазы — *shortlist* + *deep*.
При обнаружении исключающего примечания включается backtracking
с проактивной эскалацией на родительский уровень.

Отдельный модуль **НТР** определяет применимые меры (технические регламенты,
лицензирование, санитарный/ветеринарный/фитосанитарный контроль и др.) с
LLM-верификацией двойного соответствия (код + описание товара).

Дополнительно через `/tnved/lookup/{code}` возвращаются ставка ЕТТ ЕАЭС,
ставка НДС КР с учётом освобождений (НК КР ст. 289), ставка акциза КР
(НК КР ст. 336) и сводка НТР.

## Архитектура

```
Пользователь
    │
    ├──► POST /classify ──► Cascade Orchestrator ──► 10-значный код + цепочка
    │                            │  (+ сессия для needs_input)
    │                  ┌─────────┼─────────────┐
    │                  ▼         ▼             ▼
    │             OpenRouter  PostgreSQL   (Voyage AI —
    │           (любой LLM    + pgvector    зарезервировано
    │            через один   tnved_*       под прецеденты)
    │            HTTP-шлюз)   ntm_*
    │                         vat_*, excise_*
    │                         classification_sessions
    │
    ├──► POST /classify/{session_id}/answer  ── продолжение по уточнениям
    │
    ├──► GET  /tnved/lookup/{code} ── ЕТТ + НДС + акциз + НТР (без LLM)
    │
    └──► POST /ntm/verify ──► NTM Verifier ──► меры НТР со статусами
                                  │
                            ┌─────┴─────┐
                            ▼           ▼
                        PostgreSQL    LLM (light)
                         ntm_*    (двойное соответствие)
```

### Каскад классификации

| Уровень | Что определяет           | Стратегия                       | Контекст                                     | Тир   |
|---------|--------------------------|---------------------------------|----------------------------------------------|-------|
| **L0**  | Обогащение описания      | 1 шаг + SGR-карточка            | —                                            | light |
| **L1**  | Раздел (I–XXI)           | shortlist (1a) → verify (1b)    | 21 раздел, примечания кандидатов, ОПИ 1      | light |
| **L2**  | Группа (2 цифры)         | 1 шаг                           | Группы раздела + примечания, ОПИ 1           | medium|
| **L3**  | Позиция (4 цифры)        | shortlist (3a) → deep (3b)      | Позиции, пояснения по кандидатам, ОПИ 1–5    | heavy |
| **L4**  | Субпозиция (6 цифр)      | 1 шаг (auto-select при N=1)     | Субпозиции, пояснения позиции, section/cross-group definitions, ОПИ 6 | medium |
| **L5**  | Подсубпозиция (8/10)     | branch (5a) → leaf (5b), single-step при N≤5 | Ветки → коды, пояснения, definition-notes | light |

**Ключевые особенности:**
- **SGR-поля в схемах** — каждый уровень требует от модели заполнить поля
  пошагового анализа *перед* выбором кода (`physical_nature_analysis`,
  `threshold_analysis`, `code_differentiation` и т.п.).
- **needs_input на любом уровне** — если LLM возвращает непустые
  `missing_parameters`, каскад останавливается, состояние сохраняется в БД
  под `session_id`, а пользователю возвращаются уточняющие вопросы.
- **Backtracking** — backtrack-trigger от LLM откатывает каскад на нужный
  уровень с записью исключённого кода. Реализована **проактивная эскалация**
  L2 → L1 (если LLM ссылается на чужой раздел) и общая эскалация
  «уровень N был перебран → откат на N-1».
- **Auto-select** на L4/L5 при единственном дочернем коде — без LLM-вызова.
- **Целостность цепочки** проверяется пост-фактум валидатором
  (section → group → position → subposition → code_10).

## Стек технологий

- **Python 3.12+** / FastAPI / Uvicorn
- **PostgreSQL 17 + pgvector** (поле `embedding` зарезервировано под
  будущие прецеденты; для текущего каскада векторный поиск не используется
  — пояснения грузятся напрямую по коду)
- **OpenRouter** — единый OpenAI-совместимый шлюз ко всем LLM
  (Anthropic Claude, Google Gemini, MiniMax, Qwen, StepFun и др.).
  Для разных моделей провайдер выбирает одну из **трёх стратегий
  structured output** (json_schema strict / json_object + schema-in-prompt /
  plain text + schema-in-prompt) — см. `feap/llm/openrouter_provider.py`.
- **Voyage AI** — эмбеддинги (зарезервировано под прецеденты)
- **SQLAlchemy 2.0** (async) + Alembic
- **Jinja2** — шаблоны промптов (system + context-блоки)
- **Pydantic v2** — структурированные ответы LLM и валидация API

## Структура проекта

```
FEAP/
├── src/feap/                          # Основной пакет (src-layout)
│   ├── main.py                        # FastAPI-приложение, /health
│   ├── config.py                      # Settings из .env (Pydantic Settings)
│   ├── api/
│   │   ├── router.py                  # /classify, /tnved/*, /ntm/*
│   │   └── deps.py                    # DI: get_db, get_orchestrator
│   ├── cascade/
│   │   ├── orchestrator.py            # Запуск уровней, needs_input, resume
│   │   ├── backtracker.py             # Откат с эскалацией L2→L1
│   │   ├── validator.py               # Целостность цепочки + risk_flags
│   │   ├── context_builder.py         # DB → dict для промптов
│   │   └── levels/
│   │       ├── base.py                # CascadeState (+to_dict/from_dict)
│   │       ├── level0.py              # Обогащение карточки товара
│   │       ├── level1.py              # 1a shortlist → 1b verify
│   │       ├── level2.py              # Группа
│   │       ├── level3.py              # 3a shortlist → 3b deep
│   │       ├── level4.py              # Субпозиция (+ definitions)
│   │       └── level5.py              # 5a branch → 5b leaf (multi-branch)
│   ├── db/
│   │   ├── engine.py                  # async_session
│   │   ├── models.py                  # ORM-модели (tnved_*, ntm_*, vat_*, excise_*, sessions)
│   │   └── queries.py                 # Все запросы каскада + НТР + сессии
│   ├── schemas/
│   │   ├── api.py                     # ClassifyRequest/Response, NtmSummary, ...
│   │   ├── common.py                  # DecisionChainEntry, BacktrackTrigger
│   │   ├── product_card.py            # L0 (с SGR-полями)
│   │   ├── section.py                 # L1 (shortlist + result)
│   │   ├── group.py                   # L2
│   │   ├── position.py                # L3 (shortlist + deep)
│   │   ├── subposition.py             # L4 (с SGR-блоком)
│   │   ├── subsubposition.py          # L5 (branch + result)
│   │   └── validation.py              # ClassificationResult, PartialResult
│   ├── prompts/
│   │   ├── registry.py                # Jinja2-окружение
│   │   ├── assembler.py               # Сборка system + user message
│   │   └── templates/
│   │       ├── system/                # По одному на уровень/шаг + ntm_verify
│   │       └── context/               # product_card, notes, opi_rules,
│   │                                  # tree_nodes/_l4/_l5, tree_branches_l5,
│   │                                  # explanatory, decision_chain, ...
│   ├── llm/
│   │   ├── client.py                  # classify() — фасад, используется уровнями
│   │   ├── provider.py                # Абстрактный LLMProvider
│   │   ├── openrouter_provider.py     # 3 стратегии structured output + retry
│   │   └── models.py                  # resolve_model(level), tier-based + override
│   ├── ntm/
│   │   ├── schemas.py                 # NtmVerifyRequest/Response, верификации
│   │   └── verifier.py                # LLM-верификация двойного соответствия
│   └── rag/
│       ├── embedder.py                # Voyage AI через httpx
│       └── retriever.py               # pgvector (зарезервировано)
├── scripts/                            # Подготовка данных (см. ниже)
│   ├── download/                      #   1. Скачивание PDF
│   ├── parse/                         #   2. PDF/Excel → JSON
│   ├── build/                         #   3. Сборка дерева
│   ├── seed/                          #   4. Загрузка в БД
│   └── rag/                           #   (опц.) Генерация эмбеддингов
├── migrations/                         # Alembic
│   └── versions/                      #   001..006 (initial → NTM → drop verification_context)
├── tests/                              # pytest
├── data/
│   ├── TWS_TNVED_2026-02-17.xlsx      # Excel со ставками от tws.by
│   ├── ntm_seed.json                  # Меры НТР (36 мер, 157 привязок к кодам)
│   ├── ett_pdfs/                      # 96 PDF номенклатуры с eec.eaeunion.org
│   ├── pdfs/{psn,opi}/                # PDF пояснений и правил
│   ├── ntm_pdfs/                      # PDF товарных списков НТР
│   ├── reference/sections.json        # 21 раздел
│   └── parsed/                        # Промежуточные JSON: tree, leaves,
│                                      # nomenclature, notes, explanatory, opi, units
├── docs/                               # Архитектурные заметки и эксперименты
├── docker-compose.yml                  # db + migrate + app + seed
├── Dockerfile                          # python:3.12-slim
└── pyproject.toml                      # build-backend = setuptools.build_meta
```

## Быстрый старт

### Предварительные требования

- Docker и Docker Compose
- API-ключ OpenRouter (`OPENROUTER_API_KEY`) — [openrouter.ai](https://openrouter.ai)
- (Опционально) API-ключ Voyage AI (`VOYAGE_API_KEY`) — для будущего RAG

### 1. Клонирование и настройка

```bash
git clone <repo-url> && cd FEAP
cp .env.example .env
```

Отредактируйте `.env` — задайте `POSTGRES_PASSWORD` и `OPENROUTER_API_KEY`:

```env
POSTGRES_PASSWORD=your_secure_password
OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

### 2. Первый запуск

```bash
# Шаг 1: поднять БД, прогнать миграции, поднять приложение
docker compose up -d

# Шаг 2: однократно загрузить данные ТН ВЭД
docker compose run --rm seed
```

> **Что происходит при `docker compose up -d`:**
> 1. Стартует PostgreSQL (`db`) — ждёт healthcheck
> 2. Прогоняются Alembic-миграции (`migrate`) — все таблицы
> 3. Стартует FastAPI-приложение (`app`)
>
> **Что делает `seed`:**
> 1. `parse/excel.py` — Excel → `data/parsed/leaves.json` (13 279 листьев)
> 2. `build/tree.py` — `nomenclature.json` + `leaves.json` → `tree.json` (20 236 узлов)
> 3. `parse/opi.py` — генерирует `opi_rules.json`
> 4. `parse/units.py` — единицы измерения
> 5. `seed/db.py` — основной seed: дерево, ОПИ, примечания, пояснения, единицы,
>    13 типов НТР, 36 мер НТР с 157 привязками
> 6. `seed/vat_exemptions.py` — освобождения от НДС КР (НК КР ст. 289)
> 7. `seed/excise_rates.py` — ставки акциза КР (НК КР ст. 336)
>
> Все шаги idempotent — повторный запуск пропустит уже заполненные таблицы.

Приложение доступно на **http://localhost:8000**, Swagger UI на `/docs`.

### 3. Проверка

```bash
curl http://localhost:8000/health

docker compose exec db psql -U feap -d feap \
  -c "SELECT level, COUNT(*) FROM tnved_tree GROUP BY level ORDER BY level;"
# 0=Раздел=21, 1=Группа=96, 2=Позиция=1228, 3=Субпозиция=5612, 4=Лист=13279
```

### 4. Обновление номенклатуры с ЕЭК

```bash
python scripts/download/ett_pdfs.py        # Скачать 96 PDF
python scripts/parse/ett_nomenclature.py   # Пересобрать nomenclature.json
python scripts/parse/excel.py              # Если обновлён Excel ставок
python scripts/build/tree.py               # Пересборка tree.json

docker compose exec db psql -U feap -d feap \
  -c "TRUNCATE tnved_tree CASCADE; TRUNCATE tnved_opi;"
docker compose run --rm seed
```

### 5. RAG (пояснения — опционально)

Сейчас уровни L3/L4 используют пояснения через **прямую загрузку по коду**
(не RAG). Векторный поиск (`feap/rag/`) зарезервирован для будущих
прецедентов и судебных решений. Если нужно подготовить эмбеддинги впрок:

```bash
python scripts/download/psn_pdfs.py            # Скачать 97 PDF + правила
python scripts/parse/explanatory_pdfs.py       # PDF → JSON секций
python scripts/parse/notes_pdf.py              # Примечания к разделам/группам
python scripts/rag/generate_embeddings.py      # Voyage AI → embedding-колонка
```

## API

Базовый префикс: `/api/v1`.

### `POST /classify`

Запуск каскада по описанию товара.

```json
{
  "description": "Электрическая зубная щётка Oral-B с аккумулятором",
  "additional_info": {}
}
```

Возможные `status` в ответе:

| `status`      | Когда                                                          |
|---------------|----------------------------------------------------------------|
| `completed`   | Каскад прошёл до конца, заполнено `result`                     |
| `needs_input` | Каскад остановлен на уровне L0/L3/L4/L5, заполнено `partial`   |
| `error`       | Превышен лимит откатов / внутренняя ошибка, заполнено `error`  |

**Ответ `needs_input`:**

```json
{
  "session_id": "a1b2c3d4-...",
  "status": "needs_input",
  "partial": {
    "partial_code": "8509",
    "determined_to_level": 5,
    "missing_parameters": ["мощность электродвигателя"],
    "question": "Для выбора точного кода необходимо уточнить: ...",
    "questions": ["Укажите: мощность электродвигателя"],
    "classification_chain_so_far": [
      {"level": 1, "code": "XVI", "name": "...", "reasoning": "...", "confidence": 0.9},
      {"level": 2, "code": "85",  "name": "...", "reasoning": "...", "confidence": 0.85}
    ]
  }
}
```

`session_id` хранится в таблице `classification_sessions` (JSONB-снапшот
`CascadeState`) и используется эндпоинтом ниже для продолжения.

### `POST /classify/{session_id}/answer`

Продолжение каскада с ответами на уточняющие вопросы. Каскад возобновляется
не с нуля, а с уровня `stopped_at_level`.

```json
{
  "answers": {
    "мощность электродвигателя": "1.5 кВт",
    "назначение": "бытовой"
  }
}
```

Ответ имеет ту же структуру, что и `/classify` (`completed` /
`needs_input` / `error`).

Лимиты: `SESSION_MAX_ANSWERS` (по умолчанию 3) итераций уточнений на сессию,
`SESSION_TTL_MINUTES` (по умолчанию 30) — TTL сессии.

### `GET /tnved/tree?level=0` или `?parent_code=...`

Навигация по дереву. По умолчанию возвращает 21 раздел.

### `GET /tnved/lookup/{code}`

Полная справка по 10-значному коду без LLM:

- `duty_rate` — ставка ЕТТ ЕАЭС
- `vat` — ставка НДС КР с учётом освобождений (НК КР ст. 289),
  по умолчанию `VAT_RATE_DEFAULT` (12%)
- `excise` — акциз КР (НК КР ст. 336), если применим
- `unit_primary` / `unit_secondary` — единицы измерения
- `ntm` — сводка мер НТР по совпадению кода (см. ниже)

### `GET /tnved/code/{code}`

Базовые поля узла дерева (имя, уровень, ставка ЕТТ, число потомков).

### `GET /ntm/{code}`

Все потенциально применимые меры НТР для 10-значного кода
(*без* LLM-верификации). Все статусы — `may_apply`. Подходит для общего обзора.

### `POST /ntm/verify`

LLM-верификация **двойного соответствия** (код + описание товара).
Для каждой меры с непустым квалификатором LLM сопоставляет описание товара
с условием НПА и возвращает один из статусов: `applies`, `does_not_apply`,
`uncertain`. Меры без квалификатора автоматически получают `applies` по
совпадению кода.

```json
{
  "code": "8509800000",
  "product_description": "Электрическая зубная щётка, 3.7V, аккумулятор Li-Ion"
}
```

Подробности модуля: [`docs/ntm_implementation.md`](docs/ntm_implementation.md).

## Локальная разработка (без Docker)

```bash
python -m venv venv
source venv/bin/activate    # Windows (Git Bash): source venv/Scripts/activate
pip install -e ".[dev]"

docker compose up -d db     # PostgreSQL отдельно
alembic upgrade head

# Полный data-pipeline (один раз):
python scripts/download/ett_pdfs.py
python scripts/parse/ett_nomenclature.py
python scripts/parse/excel.py
python scripts/parse/opi.py
python scripts/parse/units.py
python scripts/build/tree.py
python scripts/seed/db.py
python scripts/seed/vat_exemptions.py
python scripts/seed/excise_rates.py

# Сервер
uvicorn feap.main:app --reload

# Тесты
pytest
```

## Скрипты подготовки данных

`scripts/` разбит на 5 логических подпапок. Полный pipeline:

```
download/  →  parse/  →  build/  →  seed/
                                       │
                                  (опц.) rag/
```

| Папка       | Скрипт                          | Что делает                                                         |
|-------------|---------------------------------|--------------------------------------------------------------------|
| `download/` | `ett_pdfs.py`                   | Скачивает 96 PDF номенклатуры ЕТТ с eec.eaeunion.org              |
| `download/` | `psn_pdfs.py`                   | Скачивает 97 PDF пояснений + `pravila.pdf` (ОПИ)                  |
| `parse/`    | `ett_nomenclature.py`           | PDF ЕТТ → `nomenclature.json` (имена + виртуальные узлы)          |
| `parse/`    | `excel.py`                      | TWS Excel → `leaves.json` (13 279 листьев со ставками)            |
| `parse/`    | `notes_pdf.py`                  | PDF ЕТТ → `notes.json` (примечания к разделам/группам/субпозициям)|
| `parse/`    | `explanatory_pdfs.py`           | PDF пояснений → `explanatory_notes.json` (секции по позициям)     |
| `parse/`    | `opi.py`                        | Хардкод ОПИ → `opi_rules.json`                                    |
| `parse/`    | `units.py`                      | sudact.ru → `units.json` (доп. единицы измерения)                 |
| `build/`    | `tree.py`                       | `nomenclature.json + leaves.json` → `tree.json` (20 236 узлов)    |
| `seed/`     | `db.py`                         | Главный seed: дерево, ОПИ, примечания, пояснения, единицы, НТР    |
| `seed/`     | `ntm_types.py`                  | 13 справочных типов НТР (используется `db.py` через импорт)        |
| `seed/`     | `ntm_data.py`                   | 36 мер НТР из `data/ntm_seed.json` (используется `db.py`)          |
| `seed/`     | `vat_exemptions.py`             | Освобождения от НДС КР (НК КР ст. 289)                            |
| `seed/`     | `excise_rates.py`               | Ставки акциза КР (НК КР ст. 336)                                  |
| `rag/`      | `generate_embeddings.py`        | Voyage AI → колонка `embedding` (для будущих прецедентов)         |

## Переменные окружения

### Инфраструктура

| Переменная          | Описание                          | По умолчанию    |
|---------------------|-----------------------------------|-----------------|
| `POSTGRES_USER`     | Пользователь PostgreSQL           | `feap`          |
| `POSTGRES_PASSWORD` | Пароль PostgreSQL                 | — (обязательно) |
| `POSTGRES_DB`       | Имя базы данных                   | `feap`          |
| `POSTGRES_PORT`     | Внешний порт PostgreSQL           | `5432`          |
| `APP_PORT`          | Внешний порт приложения           | `8000`          |
| `DATABASE_URL`      | URL подключения (для локальной разработки) | `postgresql+asyncpg://feap:feap@localhost:5432/feap` |

### OpenRouter (LLM)

| Переменная          | Описание                                                | По умолчанию                            |
|---------------------|---------------------------------------------------------|-----------------------------------------|
| `OPENROUTER_API_KEY`| API-ключ OpenRouter                                     | — (обязательно)                         |
| `MODEL_LIGHT`       | Тир light (L0, L1, L5, NTM verify)                      | `anthropic/claude-haiku-4-5-20251001`   |
| `MODEL_MEDIUM`      | Тир medium (L2, L4)                                     | `anthropic/claude-sonnet-4-6`           |
| `MODEL_HEAVY`       | Тир heavy (L3 — позиция)                                | `anthropic/claude-opus-4-6`             |
| `LEVEL{N}_MODEL`    | Полный override для уровня N (0–5, 99 — NTM verify)     | — (fallback на тир)                     |

### Voyage AI (зарезервировано)

| Переменная             | Описание                       | По умолчанию    |
|------------------------|--------------------------------|-----------------|
| `VOYAGE_API_KEY`       | API-ключ Voyage AI             | — (опционально) |
| `VOYAGE_MODEL`         | Модель эмбеддингов             | `voyage-3`      |
| `VOYAGE_EMBEDDING_DIM` | Размерность вектора            | `1024`          |

### Каскад / сессии / налоги

| Переменная              | Описание                                           | По умолчанию |
|-------------------------|----------------------------------------------------|--------------|
| `CASCADE_MAX_BACKTRACK` | Макс. количество откатов на запрос                 | `5`          |
| `SESSION_TTL_MINUTES`   | TTL сессии уточнений                               | `30`         |
| `SESSION_MAX_ANSWERS`   | Макс. количество итераций уточнений                | `3`          |
| `VAT_RATE_DEFAULT`      | Ставка НДС КР по умолчанию (если нет освобождения) | `12%`        |

## Дополнительная документация

- `CLAUDE.md` — детальная архитектура каскада и контекст для LLM-агентов
- `docs/ntm_implementation.md` — модуль НТР
- `docs/ett_pdf_migration.md`, `docs/naming_problem.md` — pipeline дерева
- `docs/v27_context_engineering.md` — оптимизация контекста L4/L5
- `docs/structured_output_providers.md` — стратегии structured output

## Лицензия

Проприетарный проект. Все права защищены.
