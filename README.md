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
    │      (1 товар)            │  (+ сессия для needs_input)
    │                  ┌─────────┼─────────────┐
    │                  ▼         ▼             ▼
    │             OpenRouter  PostgreSQL   (Voyage AI —
    │           (любой LLM    + pgvector    зарезервировано
    │            через один   tnved_*       под прецеденты)
    │            HTTP-шлюз,   ntm_*
    │            +семафор и   vat_*, excise_*
    │            RPM-лимит)   classification_sessions
    │                         classification_jobs / _items / _events
    │                         ocr_jobs
    │
    ├──► POST /classify/{session_id}/answer  ── продолжение по уточнениям (1 товар)
    │
    ├──► POST /classify/batch ──► sync (≤5 шт.) или async job
    │                                       │
    │                                       ▼
    │                               ┌──────────────┐
    │                               │    Redis     │ ◄── pub/sub: feap:job:{id}
    │                               │ (arq queue)  │
    │                               └──────┬───────┘
    │                          ┌───────────┼───────────┐
    │                          ▼           ▼           ▼
    │                     arq worker  arq worker  arq worker
    │                    (CascadeOrchestrator на тех же моделях)
    │
    ├──► GET  /jobs/{id}           ── статус + прогресс
    ├──► GET  /jobs/{id}/items     ── элементы батча (фильтр по status)
    ├──► POST /jobs/{id}/answers   ── ответы на needs_input для нескольких items
    ├──► GET  /jobs/{id}/events    ── SSE: live прогресс (либо polling-fallback)
    ├──► DELETE /jobs/{id}         ── отмена
    │
    ├──► POST   /ocr/jobs          ── upload 1..N файлов (PDF / image)
    │                                 │
    │                          arq: run_ocr_job
    │                          ├─ extracting  — POST ocr.trade.kg/ocr/process
    │                          ├─ structuring — LLM level 98 → ProductCard[]
    │                          └─ (опц.) auto_classify → run_ocr_split_and_enqueue
    │                                            создаёт classification_job
    │                                            (parent_ocr_job_id ссылкой)
    │    GET    /ocr/jobs/{id}     ── status + extraction + products
    │    DELETE /ocr/jobs/{id}     ── отмена
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

### Параллелизм и фоновые задачи

Помимо одиночного `POST /classify` (sync), доступен батч-эндпоинт
`POST /classify/batch` для обработки списка товаров:
- `mode=sync` (≤ `BATCH_SYNC_MAX`, default 5) — параллельно через `asyncio.gather`,
  ответ синхронно;
- `mode=async` — создаётся `Job` в Postgres, фоновый **arq**-воркер обрабатывает
  каждый item отдельной таской с идемпотентным захватом, агрегаты пересчитываются
  атомарно после каждого item;
- `mode=auto` — sync для маленьких батчей, async для больших.

Конкурентность к LLM ограничивается **на уровне ресурса**:
`asyncio.Semaphore(LLM_MAX_CONCURRENT)` per-process + опциональный
RPM-cap (`local` или Redis-backed sliding window). Лимиты сидят
**внутри** `OpenRouterProvider`, поэтому работают одинаково для sync `/classify`
и для арq-воркеров.

`needs_input` корректно работает per-item: упавшие/зависшие позиции не блокируют
остальные. SSE-эндпоинт `GET /jobs/{id}/events` стримит прогресс через Redis
pub/sub (с polling-fallback, если Redis недоступен).

Полное ТЗ — [`docs/async_architecture.md`](docs/async_architecture.md).

## Стек технологий

- **Python 3.12+** / FastAPI / Uvicorn
- **PostgreSQL 17 + pgvector** (поле `embedding` зарезервировано под
  будущие прецеденты; для текущего каскада векторный поиск не используется
  — пояснения грузятся напрямую по коду)
- **Redis 7** + **arq** — очередь фоновых задач для batch-классификации,
  pub/sub для SSE-прогресса, опциональный распределённый rate-limiter
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
│   │   ├── jobs_router.py             # /classify/batch, /jobs/* (async + SSE)
│   │   └── deps.py                    # DI: get_db, get_orchestrator, get_job_backend
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
│   │   ├── models.py                  # ORM-модели (tnved_*, ntm_*, vat_*, excise_*, sessions, classification_jobs/_items/_events)
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
│   │   ├── openrouter_provider.py     # 3 стратегии structured output + retry, semaphore + RPM-cap
│   │   ├── rate_limit.py              # Local / Redis sliding-window rate limiter
│   │   └── models.py                  # resolve_model(level), tier-based + override
│   ├── jobs/
│   │   ├── schemas.py                 # ClassifyBatchRequest, JobResponse, BatchAsync/Sync, ...
│   │   ├── repository.py              # CRUD + атомарная агрегация item-статусов в job
│   │   ├── enqueue.py                 # ArqBackend / InProcessBackend (JOBS_BACKEND)
│   │   ├── tasks.py                   # arq-таски: run_classify_batch, run_classify_item, run_resume_item
│   │   ├── worker.py                  # WorkerSettings (arq CLI entrypoint), cron-задачи
│   │   ├── events.py                  # Redis pub/sub helpers для SSE
│   │   └── sync_runner.py             # Inline-исполнитель для mode=sync
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
│   └── versions/                      #   001..008 (initial → NTM → drop verification_context → classification_jobs)
├── tests/                              # pytest (включая tests/jobs/ — state machine, rate limiter, schemas)
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
│   └── async_architecture.md          # ТЗ на job-систему (arq + Redis + SSE)
├── docker-compose.yml                  # db + redis + migrate + app + worker + seed
├── Dockerfile                          # python:3.12-slim (общий образ для app и worker)
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
> 2. Стартует Redis (`redis`) — очередь арq + pub/sub для SSE
> 3. Прогоняются Alembic-миграции (`migrate`) — все таблицы
> 4. Стартует FastAPI-приложение (`app`)
> 5. Стартует **arq worker** (`worker`, по умолчанию `WORKER_REPLICAS=2`) —
>    обрабатывает batch-классификации в фоне
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

### Авторизация (`/auth`, Track B1)

JWT (Bearer, HS256) + организации с ролями. Режим задаёт `AUTH_MODE`:

- **`optional`** (по умолчанию, dev): токен проверяется, если прислан; без
  токена работает прежнее поведение — заголовок `X-Org-Id` либо организация
  `default`, полный доступ. Ничего не ломается для существующих клиентов.
- **`required`**: все рабочие эндпоинты требуют `Authorization: Bearer <token>`
  (кроме `register`/`login`).

```
POST   /auth/register        — аккаунт + организация + токен ({email, password, name?, org_name?})
POST   /auth/login           — вход ({email, password, org_id?}); всегда generic 401
GET    /auth/me              — аккаунт, активная организация, все организации
POST   /auth/switch-org      — токен для другой своей организации
GET    /orgs/current/members            — участники (owner)
POST   /orgs/current/members            — добавить по email ({email, role}) (owner)
PATCH  /orgs/current/members/{acc_id}   — сменить роль (owner; последнего owner'а не снять)
DELETE /orgs/current/members/{acc_id}   — удалить (owner)
```

Роли: `viewer` (чтение и экспорт) < `declarant` (вся работа с ДТ и каталогом)
< `owner` (+ участники). Роль и членство перечитываются на каждом запросе —
исключение из организации действует мгновенно. Токен несёт активную
организацию; объекты чужих организаций неотличимы от несуществующих (404).
Пароли — argon2id; email нормализуется в нижний регистр.

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

### `POST /classify/batch`

Массовая классификация N товаров. Тело запроса:

```json
{
  "items": [
    {"description": "Электрическая зубная щётка Oral-B", "additional_info": {}},
    {"description": "Кофемолка ручная керамическая",     "additional_info": {}}
  ],
  "mode": "auto"
}
```

Поле `mode`:

| `mode`  | Поведение                                                                  |
|---------|----------------------------------------------------------------------------|
| `sync`  | Параллельно через `asyncio.gather`, ответ в теле. Лимит — `BATCH_SYNC_MAX` |
| `async` | Создаётся `Job`, обработка в фоновом arq-воркере. Возвращается `job_id`    |
| `auto`  | `sync` если N ≤ `BATCH_SYNC_MAX`, иначе `async`                            |

Жёсткий лимит размера батча — `BATCH_ASYNC_MAX_ITEMS` (default 200).
Превышение → `413 Payload Too Large`.

**Sync-ответ (200 OK):**

```json
{
  "results": [
    {"position": 0, "status": "completed",   "result": {...}, "session_id": null},
    {"position": 1, "status": "needs_input", "session_id": "...", "partial": {...}}
  ]
}
```

**Async-ответ (200 OK с `job_id`):**

```json
{
  "job_id": "a1b2c3d4-...",
  "status": "pending",
  "total_items": 30,
  "poll_url":   "/api/v1/jobs/a1b2c3d4-...",
  "items_url":  "/api/v1/jobs/a1b2c3d4-.../items",
  "events_url": "/api/v1/jobs/a1b2c3d4-.../events"
}
```

### `GET /jobs/{job_id}`

Метаданные job'а + агрегированный прогресс
(`total/pending/running/completed/failed/needs_input/cancelled`).

Финальные статусы: `completed`, `needs_input` (≥1 item ждёт ответа),
`partial` (часть упала, часть успешна), `failed`, `cancelled`.

### `GET /jobs/{job_id}/items?status=needs_input&limit=100&offset=0`

Список item'ов с фильтром по статусу и пагинацией. Каждый item содержит
`result` (если `completed`), `partial` + `session_id` (если `needs_input`),
или `error` (если `failed`).

### `POST /jobs/{job_id}/items/{item_id}/answer`

Ответ на уточнения для одного item'а:

```json
{ "answers": {"мощность": "1.5 кВт"} }
```

Inline-resume: вызывает `orchestrator.resume(session_id, answers)`,
обновляет item, пересчитывает агрегаты job'а, отдаёт результат.

### `POST /jobs/{job_id}/answers`

Батч-ответ для нескольких зависших позиций сразу (UI-friendly):

```json
{
  "answers": [
    {"item_id": "...", "answers": {"мощность": "1.5 кВт"}},
    {"item_id": "...", "answers": {"материал": "хлопок 100%"}}
  ]
}
```

### `GET /jobs/{job_id}/events`

**Server-Sent Events** stream — live прогресс job'а через Redis pub/sub.
События: `snapshot` (начальный), `item_done`, `item_needs_input`,
`item_failed`, `progress`, `finished`, `ping` (heartbeat каждые
`SSE_HEARTBEAT_S`). При недоступности Redis — автоматический polling-fallback.

### `DELETE /jobs/{job_id}`

Отмена job'а: помечает все pending-items как `cancelled`, job переходит
в `cancelled`. Уже `running` items штатно дорабатывают (cooperative shutdown).

### `POST /ocr/jobs`

Загрузка 1..N документов (PDF / JPEG / PNG / TIFF / WEBP). Multipart:

```
files:          бинарные файлы (обязательно ≥1, не более OCR_MAX_FILES_PER_JOB)
auto_classify:  "true" | "false"  — авто-запуск каскада после OCR (default false)
```

Ответ `202`:
```json
{
  "ocr_job_id": "...",
  "status": "pending",
  "files_count": 2,
  "auto_classify": false,
  "poll_url": "/api/v1/ocr/jobs/..."
}
```

Пайплайн в arq:
1. `extracting` — каждый файл → POST `ocr.trade.kg/ocr/process` → raw text;
2. `structuring` — один LLM-вызов (level 98, medium) → `ProductCard[]`;
3. (опц.) `auto_classify=True` → создаётся `classification_job` c `parent_ocr_job_id`
   и `ocr_jobs.linked_classification_job_id`, далее стандартный batch-pipeline.

Подробности: [`docs/ocr_integration.md`](docs/ocr_integration.md).

### `GET /ocr/jobs/{ocr_job_id}`

Статус + метаданные файлов + `extraction` (сырой текст по файлам, с warning если
OCR отдал mojibake) + `products` (извлечённые товарные карточки, если `status=completed`)
+ `linked_classification_job_id` (если был запущен каскад).

### `DELETE /ocr/jobs/{ocr_job_id}`

Отмена не-терминального OCR-job'а.

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

### Досье поставки и проект ДТ (`/shipments`, `/dt-drafts`)

Детерминированное **ДТ-ядро** (`src/feap/dt/`, без LLM): досье поставки →
расчёт таможенной стоимости (Incoterms, распределение фрахта), графа 47
(пошлина ЕТТ всех видов ставок / акциз / НДС), ФЛК-проверки → версионируемый
проект ДТ → экспорт XLSX / PDF.

```
GET    /shipments                    — список досье организации
POST   /shipments                    — создать досье (+позиции; авто-коды из каталога по SKU)
GET    /shipments/{id}               — досье с позициями
PUT    /shipments/{id}               — обновить (позиции заменяются целиком)
DELETE /shipments/{id}
POST   /shipments/{id}/clone         — копия досье (шапка+позиции, без драфтов)
POST   /shipments/{id}/classify      — классифицировать позиции без кода (batch-job,
                                       ?force=true — переклассифицировать все)
POST   /shipments/{id}/dt-draft      — собрать проект ДТ (assemble+calc+ФЛК)
GET    /dt-drafts/{id}               — графы + findings
GET    /dt-drafts/{id}/export.xlsx   — файл «по графам» для переноса в АРМ «Брокер»
GET    /dt-drafts/{id}/export.pdf    — печатная форма с водяным знаком «ПРОЕКТ»
GET    /dt-drafts/{id}/export.xml    — XML (структура R.055-draft, XSD-хук)
POST   /shipments/{id}/documents/{doc_id}/verify-permit — проверка разрешительного (TULPAR)
```

`POST /shipments/{id}/classify` создаёт стандартный batch-job
(`job_type=shipment_classify`, прогресс — `GET /jobs/{job_id}`); по завершении
каждого item'а код пишется обратно в позицию досье (`classification_ref =
job_item:{id}`), ручные коды без `force` не перезаписываются.

**Каталог товаров** — память компании «SKU → утверждённый код». При
создании/обновлении досье позиции без кода получают код из каталога
автоматически (`classification_ref = catalog:{id}`, `usage_count++`).
Результаты LLM попадают в каталог только через явный импорт (акт утверждения):

```
GET    /catalog                      — список/поиск (q=)
GET    /catalog/suggest?q=           — подсказки (только с утверждённым кодом)
POST   /catalog                      — upsert по (организация, sku)
DELETE /catalog/{id}
POST   /catalog/import-from-shipment/{id}?approved_by=  — утвердить коды из досье
```

Нехватка данных (вес нетто для комбинированной ставки, курс НБКР, доп.
единица) → `status=needs_input` + `missing_parameters`, суммы не занижаются
молча. Курсы НБКР: `python scripts/fetch/nbkr_rates.py` (ежедневно).
Организация и роль — из Bearer-токена (см. «Авторизация»); в dev-режиме
без токена — `X-Org-Id` либо `default`.
PDF требует TTF с кириллицей: автопоиск (Arial/DejaVu) или `DT_PDF_FONT_PATH`.

### SGR-слой досье (Фаза 2): документы, кросс-сверка, графа 31, советник, fixer

LLM-модули **по краям** детерминированного ядра (`dt/extractors/`,
`dt/crosscheck.py`, `dt/describe31.py`, `dt/advisor.py`, `dt/fixer.py`):
извлекают, объясняют и предлагают — деньги по-прежнему считает только код.
Все LLM-эндпоинты требуют роль `declarant`+ и метерятся в `usage_events`.

```
GET    /shipments/{id}/documents                — документы досье
POST   /shipments/{id}/documents                — привязать: {text} ИЛИ {ocr_job_id, file_index}
DELETE /shipments/{id}/documents/{doc_id}
POST   /shipments/{id}/documents/{doc_id}/extract?kind=   — типизация + извлечение
POST   /shipments/{id}/crosscheck               — сверка досье с документами
POST   /shipments/{id}/items/{position}/describe31 — предложить текст графы 31
POST   /shipments/{id}/advise                   — риск-советник
POST   /dt-drafts/{id}/fix-suggestions          — предложения правок по findings
```

Harness-контроли на каждом модуле (см. `docs/dt_core_dev_plan.md` §4):

- **Извлечение** (`extract`): типизация (уровень 90, Routing) с порогом
  уверенности `DT_DOCTYPE_CONFIDENCE_MIN` — ниже порога `needs_kind`, тип
  выбирает человек (`?kind=invoice|packing|cmr|contract|cert_origin`);
  извлечение полей (уровень 91, Cascade по типу); итоги пересчитываются
  кодом — Σ строк vs итог документа → проверки `D-xx` со статусом
  `mismatch`, не тихое доверие. Текст сканируют инъекционные сенсоры
  (документы = данные, не инструкции). Результат — в
  `shipment_documents.extracted` (JSONB, трейс и датасет).
- **Кросс-сверка**: числа сверяет только код — правила `X-01..X-06`
  (итог инвойса vs Σ цен, валюта, веса упаковочного/CMR vs позиции, места,
  Incoterms) с severity `warning`; они же автоматически попадают в findings
  каждого нового драфта. LLM (уровень 92) только нечётко сопоставляет
  стороны и строки инвойса с позициями через `match_confidence` — ниже
  `DT_MATCH_CONFIDENCE_MIN` = «не сопоставлено», а не догадка.
- **Графа 31** (уровень 93): факты собирает код (позиция, классификация,
  каталог по SKU, строки документов с точным SKU); grounding-валидатор
  требует `facts_used` ⊆ выданных id и запрещает числа, которых нет в
  фактах; отклонение → retry с фидбеком (лимит `DT_G31_MAX_REJECTIONS`),
  после лимита — `accepted=false` с причинами. Только предложение:
  применяет человек обычным PUT.
- **Советник** (уровень 94): пороговые сигналы считает код (цена/кг-выбросы
  против медианы `DT_PRICE_OUTLIER_FACTOR`, нет веса нетто, нетто=брутто,
  низкий confidence классификации); LLM формулирует объяснение +
  рекомендацию + evidence и оценивает соответствие описания коду (31↔33).
  Ответы по невыданным сигналам/позициям отбрасываются.
- **Fixer** (уровень 95): по каждому finding — `propose_value` (whitelist
  редактируемых полей) | `ask_user` | `cannot_fix`; каждое предложение
  верифицируется повторным прогоном сборки+ФЛК (`verification.verified` —
  finding исчез и новых ошибок нет). Автоприменения нет — человек правит
  досье сам.

### Форматы и жёсткие проверки (Фаза 3)

- **Классификаторы Решения 378** — таблица `ref_classifiers` (миграция 011),
  наполнение `python scripts/seed/classifiers.py`: Incoterms, виды
  транспорта, процедуры, методы ОС — verified; виды документов/платежей,
  характер сделки — `verified=false` (TODO-verify по реальной ДТ КР).
  Страны/валюты сознательно не сидируются (формат проверяют схема и F-08).
- **Графы 24/36/44** — `deal_nature` в шапке досье, `preference` на позиции;
  графа 44 собирается из документов досье автоматически (kind → код вида
  документа, `DOC_KIND_CODES`), включая статус TULPAR-проверки.
- **Полный ФЛК**: F-09 (коды по классификаторам; не загружены → одно
  info-предупреждение, не блокировка), F-10 (самопроверка арифметики графы
  47: адвалорные ставки, база НДС = ТС+пошлина+акциз, сходимость итогов),
  F-13 (преференция → подтверждающий документ в 44), F-14 (меры НТР по коду →
  разрешительный в 44; warning — применимость меры не верифицирована),
  F-15 (даты документов; контракт раньше инвойса), W-TULPAR (плохой статус
  проверки разрешительного).
- **XML-экспорт** (`export.xml`) — предварительная структура R.055-draft
  (элементы = графы 1:1, версия в `structureVersion` из
  `DT_XML_STRUCTURE_VERSION`); официальный альбом Решения ЕЭК № 75 и XSD в
  репозиторий не входят — финальный маппинг подтверждается пилотом загрузки
  в АРМ «Брокер»/KEDEN. XSD-хук: задайте `DT_XML_XSD_PATH` + `pip install
  xmlschema` — невалидный документ вернёт 422 со списком ошибок.
- **TULPAR** (`verify-permit`) — проверка разрешительных (kind `permit` /
  `cert_origin`): детерминированная проверка формата номера всегда; поиск по
  публичным реестрам swis2.trade.kg — при заданном `TULPAR_SEARCH_URL`
  (API реестров не документирован, клиент за узким интерфейсом). Результат
  кэшируется (`TULPAR_CACHE_HOURS`), пишется в `extracted.permit_check` и
  подхватывается графой 44 и W-TULPAR.

Документы: [`docs/dt_core_dev_plan.md`](docs/dt_core_dev_plan.md) (план и
архитектура), [`docs/dt_draft_spec.md`](docs/dt_draft_spec.md) (спецификация).

## Локальная разработка (без Docker)

```bash
python -m venv venv
source venv/bin/activate    # Windows (Git Bash): source venv/Scripts/activate
pip install -e ".[dev]"

docker compose up -d db redis   # PostgreSQL и Redis отдельно
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

# Сервер (single-product /classify работает сразу)
uvicorn feap.main:app --reload

# Worker для batch-классификации — отдельным процессом:
arq feap.jobs.worker.WorkerSettings

# Если не хочется поднимать Redis для smoke-тестов /classify/batch:
JOBS_BACKEND=inprocess uvicorn feap.main:app --reload
# В этом режиме батчи выполняются как asyncio-таски в самом FastAPI-процессе.
# Для production — только JOBS_BACKEND=arq.

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

### Redis / job-система

| Переменная                  | Описание                                                          | По умолчанию           |
|-----------------------------|-------------------------------------------------------------------|------------------------|
| `REDIS_URL`                 | DSN Redis для арq и pub/sub                                       | `redis://redis:6379/0` |
| `JOBS_BACKEND`              | `arq` (production) или `inprocess` (local dev без Redis)          | `arq`                  |
| `WORKER_REPLICAS`           | Кол-во worker-контейнеров (compose scale)                         | `2`                    |
| `WORKER_MAX_JOBS`           | Макс. одновременных тасков в одном воркере                        | `16`                   |
| `WORKER_JOB_TIMEOUT_S`      | Hard-timeout одного арq-таска                                     | `3600`                 |
| `WORKER_MAX_TRIES`          | Макс. попыток упавшего таска                                      | `1`                    |
| `WORKER_HEALTH_CHECK_S`     | Интервал арq health-check                                         | `30`                   |

### Конкурентность и лимиты LLM

| Переменная                  | Описание                                                          | По умолчанию |
|-----------------------------|-------------------------------------------------------------------|--------------|
| `LLM_MAX_CONCURRENT`        | Per-process semaphore на одновременные LLM-вызовы                 | `8`          |
| `LLM_RATE_LIMIT_PER_MIN`    | RPM-бюджет на ключ OpenRouter (`0` = без лимита)                  | `0`          |
| `LLM_RATE_LIMITER_BACKEND`  | `local` (per-process) / `redis` (распределённый)                  | `local`      |

### Batch / TTL

| Переменная                  | Описание                                                          | По умолчанию |
|-----------------------------|-------------------------------------------------------------------|--------------|
| `BATCH_SYNC_MAX`            | Макс. items в `mode=sync` (без очереди)                           | `5`          |
| `BATCH_ASYNC_MAX_ITEMS`     | Жёсткий лимит размера batch (защита от DoS) — иначе `413`         | `200`        |
| `JOB_RESULT_TTL_HOURS`      | TTL хранения завершённых job'ов в Postgres                        | `72`         |
| `JOB_EVENTS_RETENTION_HOURS`| TTL таблицы событий                                               | `24`         |
| `STUCK_ITEMS_RESET_AFTER_S` | После какой «тишины» `running` item возвращается в `pending`      | `900`        |
| `SSE_HEARTBEAT_S`           | Интервал heartbeat в SSE                                          | `15`         |

### Авторизация / ДТ-ядро

| Переменная               | Описание                                                             | По умолчанию |
|--------------------------|----------------------------------------------------------------------|--------------|
| `AUTH_MODE`              | `optional` (dev: токен не обязателен) \| `required`                  | `optional`   |
| `AUTH_SECRET`            | Секрет подписи JWT (HS256, ≥32 байт). Пусто в `required` — ошибка    | `""` (dev-секрет) |
| `AUTH_TOKEN_TTL_MINUTES` | Время жизни access-токена                                            | `1440` (24 ч) |
| `DT_CUSTOMS_FEE_KGS`     | Сбор за таможенное оформление, сом (пусто = строка не формируется)   | `""`         |
| `NBKR_RATES_URL`         | Ежедневный XML курсов НБКР                                           | `https://www.nbkr.kg/XML/daily.xml` |
| `DT_PDF_FONT_PATH`       | TTF с кириллицей для PDF (пусто = автопоиск Arial/DejaVu)            | `""`         |
| `DT_PDF_FONT_BOLD_PATH`  | Жирное начертание (пусто = автопоиск / regular)                      | `""`         |
| `DT_DOCTYPE_CONFIDENCE_MIN` | Порог уверенности типизации документа; ниже → `needs_kind`        | `0.6`        |
| `DT_MATCH_CONFIDENCE_MIN`   | Порог сопоставления сущностей; ниже → «не сопоставлено»           | `0.6`        |
| `DT_G31_MAX_REJECTIONS`     | Сколько раз перегенерировать графу 31 после отклонения валидатором | `2`         |
| `DT_PRICE_OUTLIER_FACTOR`   | Во сколько раз цена/кг должна отклониться от медианы для сигнала  | `4.0`        |
| `LEVEL90_MODEL`..`LEVEL95_MODEL` | Override моделей SGR-слоя ДТ (типизация 90, извлечение 91, сверка 92, графа 31 — 93, советник 94, fixer 95) | tier-fallback |
| `DT_XML_STRUCTURE_VERSION`  | Версия структуры XML-экспорта (атрибут `structureVersion`)        | `R.055-draft/1` |
| `DT_XML_XSD_PATH`           | Путь к официальному XSD (нужен `pip install xmlschema`); пусто = без валидации | `""` |
| `TULPAR_SEARCH_URL`         | URL поиска по реестрам swis2.trade.kg; пусто = только формат-проверка | `""`     |
| `TULPAR_TIMEOUT_S`          | Timeout запроса к реестру                                          | `20`         |
| `TULPAR_CACHE_HOURS`        | Свежесть кэша проверки разрешительного                             | `24`         |

### OCR-провайдер

| Переменная               | Описание                                                     | По умолчанию |
|--------------------------|--------------------------------------------------------------|--------------|
| `OCR_PROVIDER_URL`       | URL внутреннего OCR-сервиса (`/ocr/process`)                 | `http://192.168.250.44:8088/ocr/process` |
| `OCR_PROVIDER_TIMEOUT_S` | HTTP-timeout одного вызова (на крупные PDF)                  | `300`        |
| `OCR_MAX_FILE_MB`        | Лимит размера одного файла                                   | `25`         |
| `OCR_MAX_FILES_PER_JOB`  | Макс. файлов в одном OCR-job'е                               | `10`         |
| `OCR_RESULT_TTL_HOURS`   | TTL хранения OCR-job'ов в БД                                 | `72`         |
| `OCR_UPLOAD_DIR`         | Директория хранения загруженных файлов (shared volume)       | `data/ocr_uploads` |
| `OCR_ALLOWED_MIME`       | Разрешённые Content-Type через запятую                       | `application/pdf,image/jpeg,image/png,image/tiff,image/webp` |
| `LEVEL98_MODEL`          | Override модели для OCR-структурайзера (raw text → ProductCard[]) | fallback на `MODEL_MEDIUM` |

## Дополнительная документация

- `CLAUDE.md` — детальная архитектура каскада и контекст для LLM-агентов
- `docs/product_vision_v2.md` — продуктовая стратегия (рабочее место декларанта)
- `docs/dt_core_dev_plan.md` — план и архитектура ДТ-ядра (`feap.dt`)
- `docs/dt_draft_spec.md` — спецификация «Досье поставки → Проект ДТ»
- `docs/kg_customs_filing_research.md` — как подаётся ДТ в КР (системы, форматы)
- `docs/async_architecture.md` — ТЗ по job-системе (arq + Redis + SSE)
- `docs/ocr_integration.md` — модуль OCR (ocr.trade.kg + LLM-структуризатор)
- `docs/ntm_implementation.md` — модуль НТР
- `docs/ett_pdf_migration.md`, `docs/naming_problem.md` — pipeline дерева
- `docs/v27_context_engineering.md` — оптимизация контекста L4/L5
- `docs/structured_output_providers.md` — стратегии structured output

## Лицензия

Проприетарный проект. Все права защищены.
