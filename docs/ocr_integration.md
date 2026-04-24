# OCR-интеграция в FEAP — техническое задание

> Связанные документы: [`async_architecture.md`](async_architecture.md) (job-system, на котором OCR работает), [`frontend_spec.md`](frontend_spec.md) (UX загрузки документов).

## 1. Назначение

Пользователь (таможенный брокер) загружает 1..N первичных документов (инвойс, техническое описание, спецификация, упаковочный лист, контракт) — бэкенд:

1. Извлекает **сырой текст** из каждого через внутренний OCR-сервис;
2. Прогоняет один LLM-проход, который превращает сырые тексты в список товарных карточек (`ProductCard[]`);
3. Опционально автоматически запускает пачку классификаций через `classification_jobs`.

Таможенный брокер может классифицировать товар, имея **только** технические характеристики — именно такие документы дают максимально богатый вход для каскада (материал, мощность, габариты). Инвойс — промежуточный случай: N товаров в одном документе, но с короткими описаниями. Если поставщик указал HS-код в инвойсе, мы берём его как подсказку (`source_ref.hs_code_hint`), **но всегда** перепроверяем через каскад — EAEU 10-знач. коды отличаются от иностранных HS, ответственность за код на декларанте.

## 2. OCR-провайдер

Используем внутренний сервис **`ocr.trade.kg`** через простой sync-эндпоинт:

```
POST http://192.168.250.44:8088/ocr/process
Content-Type: multipart/form-data

file: <binary>
```

Ответ (HTTP 200):
```json
{
  "task_id": "uuid",
  "status": "completed",
  "success": false,                       // ← информационный флаг провайдера, не используется
  "data": {
    "filename": "...",
    "text": "...markdown со таблицами...",
    "task_id": "uuid",
    "metadata": {"page_count": 0, "source_type": "pdf"},
    "processing_time": 11.25,
    "metrics": {...}
  },
  "error": null
}
```

Вызов блокирующий (3–30 сек на файл). Мы никогда не зовём его из FastAPI-хендлера — только из arq-воркера.

**Известная проблема (mojibake):** у PDF с кириллицей, где отсутствует корректная ToUnicode/Font-map, сервис отдаёт замещающие символы `�` вместо русских букв. Байты уже потеряны на их стороне, восстановить на клиенте нельзя. В `OcrJob.extraction[i].warning` пишем диагностическое сообщение и позволяем LLM-структурайзеру работать с читаемыми частями (латиница, модели, артикулы). В перспективе — отдельный тикет OCR-команде на принудительный image-based OCR для таких PDF.

## 3. Архитектура потока

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend: POST /api/v1/ocr/jobs (multipart, N files)       │
└─────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  OcrJob[pending]  (status, files_meta, auto_classify)       │
│  файлы на диске: data/ocr_uploads/<job_id>/...              │
│  backend.enqueue_ocr_job(job_id) → arq                      │
└─────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  arq: run_ocr_job                                           │
│  ───────────────────                                        │
│  stage=extracting: для каждого файла →                      │
│       POST /ocr/process → OcrFileExtraction                 │
│       (text, processing_time_s, warning)                    │
│  stage=structuring: один LLM-вызов (level 98) →             │
│       StructuredProducts(products, skipped_lines, ...)      │
│  stage=completed                                             │
└─────────────────────────────────────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
     (если auto_classify=False)    (если auto_classify=True)
     пользователь смотрит          run_ocr_split_and_enqueue
     /ocr/jobs/{id}, редактирует   создаёт ClassificationJob
     в UI, вручную жмёт            (parent_ocr_job_id → ocr_job)
     POST /classify/batch          и enqueue run_classify_batch
```

## 4. Модуль `feap/ocr/`

```
src/feap/ocr/
├── __init__.py
├── client.py         # OcrClient — httpx async, POST /ocr/process
├── errors.py         # OcrError, OcrProviderError, OcrProviderTimeout, ...
├── repository.py     # CRUD для ocr_jobs
├── schemas.py        # Pydantic: OcrProviderResponse, OcrFileExtraction,
│                     #   StructuredProduct, StructuredProducts, API envelopes
├── service.py        # run_extraction(), run_structure() — чистая оркестрация
├── storage.py        # Локальное хранение файлов (data/ocr_uploads/<id>/)
└── structurer.py     # LLM level 98 — raw text → StructuredProducts
```

`service.py` не знает про Redis/ctx — это делает `feap/jobs/tasks.py::run_ocr_job`, который дёргает сервис и маркирует стадии. Такой split позволяет юнит-тестировать сервис с `_FakeDB` и `_StubClient`.

## 5. Структура данных (`ocr_jobs`)

```sql
CREATE TABLE ocr_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status VARCHAR(32) NOT NULL,
    stage VARCHAR(32),
    files JSONB NOT NULL DEFAULT '[]',
        -- [{filename, size_bytes, sha256, content_type, stored_path}]
    auto_classify BOOLEAN NOT NULL DEFAULT false,
    params JSONB NOT NULL DEFAULT '{}',
    extraction JSONB,
        -- [{file_index, filename, text, processing_time_s, provider_task_id, warning}]
    products JSONB,
        -- [{product_card: {...ProductCard}, source_ref: {file_index, line_hint, hs_code_hint, ...}}]
    skipped_notes JSONB,
    linked_classification_job_id UUID,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
```

Миграция — `migrations/versions/007_ocr_jobs.py`. Она же добавляет FK `classification_jobs.parent_ocr_job_id → ocr_jobs.id` (сама колонка создана в 008 как plain UUID без FK — там был перекрёстный зависимый цикл).

Статусы: `pending` → `extracting` → `structuring` → `completed | failed | cancelled`.

## 6. Эндпоинты

### `POST /api/v1/ocr/jobs`

Multipart upload 1..N файлов.

```
Content-Type: multipart/form-data

files[]:        бинарные файлы (PDF / JPEG / PNG / TIFF / WEBP)
auto_classify:  "true" | "false" — по умолчанию false
```

Валидация:
- Размер каждого файла ≤ `OCR_MAX_FILE_MB` (default 25 MB).
- Количество файлов ≤ `OCR_MAX_FILES_PER_JOB` (default 10).
- `Content-Type` входит в `OCR_ALLOWED_MIME`.

Ответ `202 Accepted`:
```json
{
  "ocr_job_id": "...",
  "status": "pending",
  "files_count": 2,
  "auto_classify": false,
  "poll_url": "/api/v1/ocr/jobs/..."
}
```

### `GET /api/v1/ocr/jobs/{ocr_job_id}`

```json
{
  "ocr_job_id": "...",
  "status": "completed",
  "stage": "completed",
  "auto_classify": false,
  "files": [{"filename": "1_1.pdf", "size_bytes": 358467, "sha256": "...", "content_type": "application/pdf"}],
  "extraction": [
    {
      "file_index": 0,
      "filename": "1_1.pdf",
      "text": "...",
      "processing_time_s": 11.25,
      "provider_task_id": "...",
      "warning": null
    }
  ],
  "products": [
    {
      "product_card": {
        "original_description": "OceanStor Dorado 6000 V6 D6V6-1T-NVMe",
        "normalized_description": "Система хранения данных OceanStor Dorado 6000 V6",
        "material": null, "function": "СХД — хранение данных",
        "has_electric_motor": false, "power_source": "электричество",
        "intended_use": "промышленный",
        "missing_info": [], "clarifying_questions": []
      },
      "source_ref": {
        "file_index": 0, "line_hint": 1, "quantity": 1,
        "unit_price": 93206, "currency": "EUR"
      }
    }
  ],
  "skipped_notes": ["pp. bank details, TOTAL row"],
  "linked_classification_job_id": null,
  "error": null,
  "created_at": "...", "started_at": "...", "completed_at": "...", "expires_at": "..."
}
```

### `DELETE /api/v1/ocr/jobs/{ocr_job_id}`

Отмена не-терминального job'а. Уже запущенные таски штатно дорабатывают (cooperative shutdown).

## 7. Цепочка OCR → Classify

При `auto_classify=true`:

1. `run_ocr_job` после `stage=structuring` энкьюит `run_ocr_split_and_enqueue(ocr_job_id)`.
2. `run_ocr_split_and_enqueue` читает `ocr_jobs.products`, создаёт `classification_jobs` (`job_type='ocr_classify'`, `parent_ocr_job_id=ocr_job_id`), заполняет items из `ProductCard`, обновляет `ocr_jobs.linked_classification_job_id`.
3. Стандартный batch-pipeline: arq `run_classify_batch` → `run_classify_item` × N.

UI опирается на `linked_classification_job_id`, чтобы перевести пользователя с OCR-прогресса на classification-прогресс (SSE/polling остаются теми же).

## 8. LLM-структуризатор (level 98)

Один вызов на все файлы пакета. Тир `medium` (Claude Sonnet по умолчанию). Переопределение — `LEVEL98_MODEL`.

Промпт: `src/feap/prompts/templates/system/level98_ocr_structure.jinja2`. Инструкция:
- Определить тип каждого документа (инвойс / спецификация / контракт / packing list / tech description / other).
- Для табличных (инвойс, packing list) — каждая строка товара → отдельный `ProductCard`.
- Для одиночных (tech description) — один `ProductCard` с богатым заполнением.
- HS-код из инвойса складывать в `source_ref.hs_code_hint`, не использовать как финальный.
- Quantity, unit, unit_price, currency, country_origin → `source_ref` (не в карточку).
- Услуги (доставка, упаковка) и totals — в `skipped_lines`.
- Если текст нечитаем (сплошные `�`) — вернуть пустой `products` с сообщением.

Output schema: `StructuredProducts` (см. `feap/ocr/schemas.py`).

## 9. Конфигурация

| Переменная              | Описание                                              | Default |
|-------------------------|-------------------------------------------------------|---------|
| `OCR_PROVIDER_URL`      | URL внутреннего OCR                                   | `http://192.168.250.44:8088/ocr/process` |
| `OCR_PROVIDER_TIMEOUT_S`| HTTP-timeout одного вызова                            | `300`   |
| `OCR_MAX_FILE_MB`       | Лимит размера одного файла                            | `25`    |
| `OCR_MAX_FILES_PER_JOB` | Макс. файлов в одном job'е                            | `10`    |
| `OCR_RESULT_TTL_HOURS`  | TTL `ocr_jobs` в БД                                   | `72`    |
| `OCR_UPLOAD_DIR`        | Директория хранения файлов                            | `data/ocr_uploads` |
| `OCR_ALLOWED_MIME`      | Разрешённые Content-Type через запятую                | `application/pdf,image/jpeg,image/png,image/tiff,image/webp` |
| `LEVEL98_MODEL`         | Override модели для OCR-структурайзера                | fallback на `MODEL_MEDIUM` |

## 10. Хранилище файлов

**MVP:** локальная директория (`data/ocr_uploads/<ocr_job_id>/<sha[:16]>_<filename>`). В `docker-compose.yml` примонтирован как named volume `ocr_uploads:/app/data/ocr_uploads`, разделяемый между `app` и `worker` контейнерами.

Ограничение: работает только при развёртывании на одном хосте (или в k8s с `ReadWriteMany` volume). Для multi-node production — swap на S3/MinIO. Абстракция в `feap/ocr/storage.py` спроектирована под drop-in замену: `save_files / read_file / delete_job_dir` — три функции, можно реимплементировать через `boto3`/`minio`.

## 11. Обработка ошибок

| Ситуация                             | Что делаем |
|--------------------------------------|------------|
| Файл > `OCR_MAX_FILE_MB`              | `413 Payload Too Large` на upload, job не создаётся |
| Неподдерживаемый MIME                 | `415 Unsupported Media Type` |
| Больше `OCR_MAX_FILES_PER_JOB` файлов | `413` |
| Провайдер 5xx                         | В `OcrFileExtraction.warning`, не блокирует другие файлы |
| Провайдер timeout                     | Тот же путь, `warning="provider error: ..."` |
| Все файлы вернули пустой текст        | Структурайзер возвращает `products=[]`, job=completed, skipped_notes объясняют |
| Mojibake (`�` в тексте)               | Warning `"N unrecoverable glyphs"`, текст всё равно идёт в LLM |
| LLM-структурайзер упал                | Job=failed с причиной. `extraction` уже сохранён → retry возможен без повторного OCR. |
| Enqueue arq-таска упал                | `503`, job=failed с `enqueue failed: ...` |

## 12. Тесты

- `tests/ocr/test_schemas.py` — round-trip фикстур провайдера через pydantic.
- `tests/ocr/test_client.py` — httpx.MockTransport, 5xx/4xx/timeout/non-JSON → правильные exception'ы.
- `tests/ocr/test_storage.py` — лимиты размера/числа/пустые файлы.
- `tests/ocr/test_service.py` — `run_extraction` + `run_structure` с моками БД/клиента.

Integration-тесты (реальный OCR + реальный LLM + реальная Postgres) — отдельно в CI, не в unit pipeline. Фикстуры реальных ответов сервиса — `tests/fixtures/ocr/*.json`.

## 13. Открытые вопросы / будущее

1. **Mojibake fix.** Тикет OCR-команде на image-OCR fallback для PDF без ToUnicode.
2. **Дедупликация при повторной загрузке** — по `sha256`. Пока не реализовано.
3. **Webhook от провайдера** — нет, у сервиса polling-free sync-модель.
4. **S3/MinIO** — P2. Разделимый volume между app и worker на MVP.
5. **Retry-extract endpoint** — повторный запуск только LLM-структурайзера по уже сохранённым `extraction`, без повторного OCR. Полезно когда обновили промпт.
6. **Идемпотентность на создание job'а** (`Idempotency-Key`) — P2, чтобы двойной submit с фронта не создавал два job'а.
