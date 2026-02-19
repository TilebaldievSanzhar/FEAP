# FEAP — Foreign Economic Activity Platform

Каскадный LLM-классификатор товаров по **ТН ВЭД ЕАЭС** (10-значные коды таможенного тарифа).

Система последовательно спускается по дереву номенклатуры (раздел → группа → позиция → субпозиция → подсубпозиция), на каждом шаге вызывая LLM с релевантным контекстом: примечаниями, пояснениями и ОПИ. При обнаружении исключений запускается backtracking.

## Архитектура

```
Пользователь
    │
    ▼
 FastAPI  ──►  Cascade Orchestrator
                  │
        ┌─────────┼─────────────┐
        ▼         ▼             ▼
   Anthropic   PostgreSQL    Voyage AI
    (Claude)   + pgvector   (embeddings)
```

### Каскад классификации

| Уровень | Что определяет         | Контекст                     | Модель |
|---------|------------------------|------------------------------|--------|
| 0       | Обогащение описания    | —                            | Haiku  |
| 1       | Раздел (I–XXI)         | Дерево, примечания, ОПИ 1    | Haiku  |
| 2       | Группа (2 цифры)       | Группы раздела, примечания   | Haiku/Sonnet |
| 3       | Позиция (4 цифры)      | Позиции, пояснения (RAG), ОПИ 1–5 | Sonnet/Opus |
| 4       | Субпозиция (6 цифр)    | Субпозиции, пояснения, ОПИ 6 | Sonnet |
| 5       | Подсубпозиция (8/10)   | Технические параметры        | Haiku  |

## Стек технологий

- **Python 3.12+** / FastAPI / Uvicorn
- **PostgreSQL 17** + pgvector (векторный поиск)
- **Anthropic Claude** — каскадная классификация (Structured Outputs)
- **Voyage AI** — эмбеддинги для RAG
- **SQLAlchemy 2.0** (async) + Alembic (миграции)
- **Jinja2** — шаблоны промптов
- **Pydantic v2** — валидация и схемы ответов

## Структура проекта

```
FEAP/
├── src/feap/                  # Основной пакет (src-layout)
│   ├── main.py                # FastAPI-приложение
│   ├── config.py              # Настройки (из .env)
│   ├── api/                   # Эндпоинты
│   │   ├── router.py          # POST /classify, GET /tnved/tree, ...
│   │   └── deps.py            # Dependency injection
│   ├── cascade/               # Ядро каскада
│   │   ├── orchestrator.py    # Оркестратор (запуск всех уровней)
│   │   ├── backtracker.py     # Логика отката
│   │   ├── context_builder.py # Сборка контекста для LLM
│   │   └── levels/            # Обработчики уровней 0–5
│   ├── db/                    # Слой БД
│   │   ├── engine.py          # AsyncPG engine
│   │   ├── models.py          # ORM-модели
│   │   └── queries.py         # Запросы
│   ├── schemas/               # Pydantic-модели (по уровням)
│   ├── prompts/               # Промпты
│   │   ├── registry.py        # Реестр шаблонов
│   │   ├── assembler.py       # Сборка system + user
│   │   └── templates/         # Jinja2-шаблоны
│   ├── llm/                   # Клиент Anthropic API
│   └── rag/                   # RAG (эмбеддинги + поиск)
├── scripts/                   # Подготовка данных
│   ├── parse_excel.py         # Excel → JSON
│   ├── build_tree.py          # Построение дерева
│   ├── seed_db.py             # Загрузка в БД
│   └── ...                    # Парсинг PDF, генерация эмбеддингов
├── migrations/                # Alembic-миграции
├── tests/                     # Тесты (pytest)
├── data/                      # Данные (Excel, PDF, JSON)
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## Быстрый старт

### Предварительные требования

- Docker и Docker Compose
- API-ключ Anthropic (`ANTHROPIC_API_KEY`)
- (Опционально) API-ключ Voyage AI (`VOYAGE_API_KEY`) — для RAG

### 1. Клонирование и настройка

```bash
git clone <repo-url> && cd FEAP
cp .env.example .env
```

Отредактируйте `.env` — задайте `POSTGRES_PASSWORD` и `ANTHROPIC_API_KEY`:

```env
POSTGRES_PASSWORD=your_secure_password
ANTHROPIC_API_KEY=sk-ant-your-key-here
VOYAGE_API_KEY=pa-your-key-here   # опционально, для RAG
```

### 2. Первый запуск

```bash
# Шаг 1: поднять всё (PostgreSQL → миграции → приложение — автоматически)
docker compose up -d

# Шаг 2: загрузить данные ТН ВЭД (разово, при первом деплое)
docker compose run --rm seed
```

> **Что происходит при `docker compose up -d`:**
> 1. Стартует PostgreSQL (`db`) — ждёт healthcheck
> 2. Автоматически применяются Alembic-миграции (`migrate`) — создаются все таблицы
> 3. Стартует FastAPI-приложение (`app`) — только после успешных миграций
>
> **Что делает `seed`:** парсит Excel с 13 279 листовыми кодами → строит иерархию
> (21 раздел → 96 групп → 1 228 позиций → 5 612 субпозиций → листья) → загружает
> в `tnved_tree`. Также загружает правила ОПИ 1–6 в `tnved_opi`. Безопасно
> запускать повторно — пропустит таблицы, в которых уже есть данные.

Приложение доступно по адресу: **http://localhost:8000**

### 3. Проверка

```bash
# Health-check
curl http://localhost:8000/health

# Swagger UI
open http://localhost:8000/docs

# Количество загруженных кодов в БД
docker compose exec db psql -U feap -d feap \
  -c "SELECT level, COUNT(*) FROM tnved_tree GROUP BY level ORDER BY level;"
```

### 4. Обновление

```bash
# После изменений кода — пересборка и рестарт (миграции применятся автоматически)
docker compose up -d --build

# После обновления Excel-файла номенклатуры — принудительная перезагрузка
docker compose exec db psql -U feap -d feap -c "TRUNCATE tnved_tree CASCADE; TRUNCATE tnved_opi;"
docker compose run --rm seed
```

### 5. RAG (пояснения к ТН ВЭД — опционально)

Для работы уровней L3/L4 с контекстом пояснений нужно загрузить PDF-файлы ЕЭК.
Это разовый длительный процесс (~97 файлов, несколько часов на эмбеддинги):

```bash
# Скачать PDF пояснений с сайта ЕЭК
docker compose run --rm seed python scripts/download_pdfs.py

# Распарсить PDF → структурированные чанки
docker compose run --rm seed python scripts/parse_explanatory_pdfs.py

# Распарсить примечания к разделам/группам
docker compose run --rm seed python scripts/parse_notes_pdf.py

# Сгенерировать эмбеддинги через Voyage AI и загрузить в pgvector
docker compose run --rm seed python scripts/generate_embeddings.py
```

> Без этого шага классификация работает, но на уровнях L3/L4 не будет
> контекста пояснений из RAG — только дерево и примечания.

## API

### `POST /api/v1/classify`

Классификация товара по текстовому описанию.

```json
{
  "description": "Электрическая зубная щётка Oral-B с аккумулятором",
  "additional_info": null
}
```

Ответ — 10-значный код с цепочкой обоснований по каждому уровню каскада.

### `GET /api/v1/tnved/tree?level=0`

Навигация по дереву ТН ВЭД. Параметры: `level` (уровень) или `parent_code` (потомки узла).

### `GET /api/v1/tnved/code/{code}`

Детали конкретного кода.

## Локальная разработка (без Docker)

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -e ".[dev]"

# Запустить PostgreSQL отдельно (Docker или локально)
docker compose up -d db

# Миграции
alembic upgrade head

# Загрузка данных
python scripts/parse_excel.py
python scripts/build_tree.py
python scripts/seed_db.py

# Запуск сервера
uvicorn feap.main:app --reload

# Тесты
pytest
```

## Переменные окружения

### Инфраструктура

| Переменная          | Описание                        | По умолчанию |
|---------------------|---------------------------------|--------------|
| `POSTGRES_USER`     | Пользователь PostgreSQL         | `feap`       |
| `POSTGRES_PASSWORD` | Пароль PostgreSQL               | — (обязательно) |
| `POSTGRES_DB`       | Имя базы данных                 | `feap`       |
| `POSTGRES_PORT`     | Внешний порт PostgreSQL         | `5432`       |
| `APP_PORT`          | Внешний порт приложения         | `8000`       |
| `DATABASE_URL`      | URL подключения (для локальной разработки) | `postgresql+asyncpg://feap:feap@localhost:5432/feap` |

### Anthropic (LLM)

| Переменная          | Описание                        | По умолчанию |
|---------------------|---------------------------------|--------------|
| `ANTHROPIC_API_KEY` | API-ключ Anthropic Claude       | — (обязательно) |
| `LLM_MODEL_LIGHT`  | Модель для простых уровней (L0, L1, L5) | `claude-haiku-4-5-20251001` |
| `LLM_MODEL_MEDIUM` | Модель для средних уровней (L2, L4)     | `claude-sonnet-4-6` |
| `LLM_MODEL_HEAVY`  | Модель для сложного уровня (L3 — позиция) | `claude-opus-4-6` |

### Voyage AI (эмбеддинги)

| Переменная             | Описание                       | По умолчанию |
|------------------------|--------------------------------|--------------|
| `VOYAGE_API_KEY`       | API-ключ Voyage AI             | — (опционально) |
| `VOYAGE_MODEL`         | Модель эмбеддингов             | `voyage-3`   |
| `VOYAGE_EMBEDDING_DIM` | Размерность вектора            | `1024`       |

### Каскад

| Переменная              | Описание                             | По умолчанию |
|-------------------------|--------------------------------------|--------------|
| `CASCADE_MAX_BACKTRACK` | Макс. количество откатов на сессию   | `3`          |

## Лицензия

Проприетарный проект. Все права защищены.
