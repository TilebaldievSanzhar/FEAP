# OCR-интеграция в FEAP — техническое задание

> Связанные документы: [`async_architecture.md`](async_architecture.md) (job-system, на котором OCR работает), [`frontend_spec.md`](frontend_spec.md) (UX загрузки документов).

## 1. Назначение

Текущий `POST /classify` принимает только текстовое описание товара. Для индустриального сценария критично уметь принимать **первичные документы** (инвойсы, спецификации, контракты, упаковочные листы, ГТД, товаросопроводительные) и автоматически:

1. Извлечь из них товарные позиции;
2. Нормализовать каждую позицию в карточку, пригодную для каскада;
3. Запустить классификацию по N товарам сразу.

Эта задача требует промежуточного слоя — **OCR + структурное извлечение**. У провайдера есть две модели API; нужно выбрать одну и встроить её в FEAP без потери асинхронной природы текущего бэкенда.

## 2. Анализ вариантов API провайдера OCR

### Вариант A — синхронный
- Один HTTP-запрос: документ → ответ с **markdown-текстом**.
- Плюсы: 1 round-trip, простой happy-path.
- Минусы:
  1. Markdown — это **сырой текст без семантики**. Извлечение позиций инвойса из markdown потребует *ещё одного* LLM-вызова (или хрупкого regex-парсера) — то есть мы перекладываем задачу с провайдера обратно на себя.
  2. Блокирующий HTTP-запрос на крупный документ (10+ страниц) держит TCP-соединение надолго → таймауты, retry-сложности, нагрузка на воркер.
  3. Невозможно показать пользователю прогресс.
  4. Нет нативной возможности параллельно обрабатывать несколько документов на одной сессии.

### Вариант B — асинхронный (submit → poll → result)
- Шаг 1: `POST /ocr` с файлом → `{task_id}`.
- Шаг 2: `GET /ocr/{task_id}` → пока не готово; когда готово — **структурированный JSON** (поля зависят от типа документа: для инвойса — список line_items с product_name, qty, unit, price, hs_code_hint и т.д.).
- Плюсы:
  1. Структурированные данные — *ровно то, что нужно для splitter'а*. Никакого парсинга markdown.
  2. Любой размер документа без блокирующего соединения.
  3. Прогресс легко мапится в наш job-status.
  4. Параллельная обработка нескольких документов — провайдер сам очередью занимается.
  5. Совпадает с архитектурой нашего batch-классификатора (см. [`async_architecture.md`](async_architecture.md#3-job-system)) — тот же паттерн polling, единый UX-компонент во фронтенде.

### Решение: **Вариант B (асинхронный со структурированным выходом)**

Приоритеты решения, в порядке убывания веса:
1. **Структурированный выход — это фундамент.** Markdown потребует LLM-парсинг, который удвоит latency, цену и точку отказа.
2. **Архитектурная согласованность.** Бэкенд уже async; job-pattern будет нужен и для batch-классификации — переиспользуем единый механизм.
3. **UX.** Прогресс-бар на загрузке многостраничного документа — стандартное ожидание пользователя в 2026.
4. **Надёжность.** Polling с экспоненциальным backoff устойчивее, чем 5-минутный HTTP-запрос.

**Sync-вариант (A) НЕ используем.** Даже не как fallback: смешение двух стратегий усложняет код без выигрыша. Если у провайдера sync есть для маленьких документов, мы всё равно вызываем async API — провайдер сам быстро ответит на маленький документ.

## 3. Архитектура интеграции

### 3.1. Модуль `feap/ocr/`

```
src/feap/ocr/
├── __init__.py
├── client.py              # OcrClient — обёртка над HTTP API провайдера
├── schemas.py             # Pydantic-модели ответов провайдера (по типам)
├── document_types.py      # Enum DocumentType + маппинги
├── splitter.py            # LLM-разделитель: structured OCR → list[ProductCard]
├── service.py             # OcrService — high-level use-cases
└── errors.py              # OcrError, OcrTimeoutError, OcrUnsupportedDocument
```

### 3.2. Поток данных

```
Frontend
   │ multipart/form-data: file + document_type_hint
   ▼
POST /api/v1/ocr/jobs
   │ создаёт OcrJob (БД), отдаёт ocr_job_id
   │ запускает background task
   ▼
OcrService.process(file, type_hint)
   ├─► OcrClient.submit(file)            # → provider task_id
   ├─► OcrClient.poll(task_id)           # пока готово (с backoff)
   ├─► OcrSchemas.parse(raw_response)    # типизированный объект
   ├─► OcrSplitter.extract_products()    # LLM, режет на N карточек
   └─► сохраняет результат в OcrJob.result_json
                  │
                  ▼ (опционально, если в запросе chain=true)
         ClassificationJob (см. async_architecture.md)
```

### 3.3. `OcrClient` — HTTP-обёртка

```python
# src/feap/ocr/client.py
class OcrClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def submit(
        self,
        file: bytes,
        filename: str,
        document_type: DocumentType | None = None,
    ) -> str:
        """Отправляет документ на OCR. Возвращает provider task_id."""

    async def poll(
        self,
        task_id: str,
        *,
        max_wait: float = 600.0,
        initial_interval: float = 1.0,
        max_interval: float = 10.0,
    ) -> dict:
        """Опрашивает провайдера до получения результата.
        
        Backoff: 1s → 1.5s → 2.25s → ... → max 10s.
        Завершается успехом, ошибкой или OcrTimeoutError.
        """

    async def close(self) -> None:
        await self._client.aclose()
```

**Важно:**
- `httpx.AsyncClient` (не `httpx.Client`) — соответствует FastAPI async-стилю.
- Один экземпляр клиента на приложение (синглтон через DI), connection pool переиспользуется.
- `poll` — *активный* polling провайдера, не путать с polling нашего job-status фронтендом.

### 3.4. `OcrSchemas` — типизация по типам документов

OCR-провайдер для разных типов документов отдаёт разные структуры. Закладываем минимум 4 типа:

```python
# src/feap/ocr/document_types.py
class DocumentType(str, Enum):
    INVOICE = "invoice"                 # инвойс / commercial invoice
    CUSTOMS_DECLARATION = "customs"     # ГТД / EAD
    SPECIFICATION = "specification"     # техническая спецификация / packing list
    CONTRACT = "contract"               # внешнеторговый контракт
    UNKNOWN = "unknown"                 # auto-detect
```

```python
# src/feap/ocr/schemas.py
class InvoiceLineItem(BaseModel):
    line_number: int | None = None
    product_name: str                       # обязательное
    description_extra: str | None = None    # доп. строки описания
    quantity: float | None = None
    unit: str | None = None
    unit_price: float | None = None
    total_price: float | None = None
    currency: str | None = None
    country_of_origin: str | None = None
    hs_code_hint: str | None = None         # если поставщик уже указал
    raw_row: dict[str, Any] | None = None   # сырая строка от провайдера

class InvoiceOcrResult(BaseModel):
    document_type: Literal["invoice"]
    seller: str | None = None
    buyer: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    line_items: list[InvoiceLineItem]
    total_amount: float | None = None
    currency: str | None = None
    raw_text: str | None = None             # markdown для отладки

# Аналогично: CustomsOcrResult, SpecificationOcrResult, ContractOcrResult.
# Все наследуются от OcrResultBase с общими полями: pages, confidence, raw_text.
```

**Зачем держать `raw_row` и `raw_text`:** провайдер может ошибиться в маппинге; мы должны иметь возможность переразбора без повторного OCR.

### 3.5. `OcrSplitter` — извлечение товарных карточек

Между «структурированный OCR» и «N запусков каскада» лежит *splitter*. Он решает:
- Очистить product_name от мусора (артикулы, страны, упаковка → отдельные поля);
- Определить, что это **один товар**, а не комплект (для комплектов выставить флаг для ОПИ 3б);
- Заполнить поля карточки L0 (см. `feap/schemas/product_card.py`) — material, function, dimensions, processing_degree и т.д.;
- Если поставщик указал hs_code_hint — записать его как *подсказку* (не auto-pick — каскад всё равно проверит).

**Реализация:** один LLM-вызов на batch строк документа (а не по 1 вызову на строку — экономия токенов). Промпт:

```jinja
# src/feap/prompts/templates/system/ocr_split.jinja2
Ты получаешь структурированный JSON из OCR коммерческого инвойса.
Для каждой строки line_items сформируй карточку товара (см. схему ProductCard).

Правила:
1. Объединяй продолжения описания (description_extra) в normalized_description.
2. Если в строке указано несколько товаров через ";" или нумерацию — раздели их.
3. Если строка содержит услугу (доставка, упаковка) — пропусти её.
4. hs_code_hint сохраняй только если поставщик его указал.
5. Если данных в строке недостаточно — заполни missing_info и clarifying_questions.

Верни массив карточек ровно в порядке line_items (с возможным расщеплением).
```

Структурированный ответ:
```python
class SplitterOutput(BaseModel):
    products: list[ProductCard]
    skipped_lines: list[int] = []          # услуги, пустые строки
    notes: str | None = None               # общие наблюдения
```

LLM-тир: **medium** (требуется аккуратное чтение, но не глубокое рассуждение). Используем `LEVEL_OCR_SPLIT_MODEL` env-переменную (см. §6).

### 3.6. `OcrService` — high-level use-case

```python
# src/feap/ocr/service.py
class OcrService:
    def __init__(
        self,
        client: OcrClient,
        splitter: OcrSplitter,
        db_session_factory: Callable[[], AsyncSession],
    ):
        ...

    async def process_document(
        self,
        ocr_job_id: str,
        file: bytes,
        filename: str,
        document_type: DocumentType,
    ) -> None:
        """Полный цикл: submit → poll → parse → split.
        
        Запускается из background task. Обновляет OcrJob в БД на каждом
        этапе (status: uploading → ocr_processing → extracting → completed).
        """
```

Этот метод **не запускает классификацию**. Цепочка с классификацией — отдельная сущность (см. §5 ниже и [`async_architecture.md`](async_architecture.md#7-цепочка-ocr--classify)).

## 4. Эндпоинты

### `POST /api/v1/ocr/jobs`

```
Content-Type: multipart/form-data

file:               двоичный файл (PDF/JPG/PNG/TIFF)
document_type:      string, optional ("invoice"|"customs"|...)
auto_classify:      bool, optional, default=false
                    если true — после OCR автоматически создаётся
                    ClassificationJob и его ID попадает в статус
```

**Лимиты:**
- Размер файла: `OCR_MAX_FILE_MB` (default 25 MB);
- Допустимые типы: PDF, JPG, JPEG, PNG, TIFF, WEBP;
- Опционально: счётчик активных OCR-задач на пользователя (когда появится auth).

**Ответ:**
```json
{
  "ocr_job_id": "ocr_a1b2c3d4-...",
  "status": "pending"
}
```

### `GET /api/v1/ocr/jobs/{ocr_job_id}`

```json
{
  "ocr_job_id": "ocr_a1b2c3...",
  "status": "completed",                 // pending | uploading | ocr_processing | extracting | completed | failed
  "document_type": "invoice",
  "progress": {
    "stage": "extracting",
    "stage_index": 3,
    "stage_count": 4,
    "message": "Извлечение товарных карточек (LLM)"
  },
  "result": {
    "ocr_data": { ...InvoiceOcrResult... },
    "products": [ ...ProductCard[] ... ],
    "skipped_lines": [4, 7]
  },
  "linked_classification_job_id": "cls_x1y2z3...",  // если auto_classify=true
  "error": null,
  "created_at": "2026-04-22T10:00:00Z",
  "completed_at": "2026-04-22T10:00:42Z"
}
```

### `DELETE /api/v1/ocr/jobs/{ocr_job_id}`

Отмена задачи (если ещё в pending/processing). Завершённые удаляются по TTL.

### `GET /api/v1/ocr/jobs` *(опционально, для админки)*

Список задач с фильтрами `status`, `created_after`, пагинация.

## 5. Хранилище

Таблица `ocr_jobs`:

```sql
CREATE TABLE ocr_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status VARCHAR(32) NOT NULL,           -- pending | uploading | ocr_processing | extracting | completed | failed | cancelled
    document_type VARCHAR(32),
    filename VARCHAR(255) NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    file_sha256 CHAR(64) NOT NULL,         -- для дедупликации
    provider_task_id VARCHAR(128),         -- task_id у OCR-провайдера
    progress JSONB,                        -- {stage, message, stage_index, stage_count}
    result JSONB,                          -- {ocr_data, products, skipped_lines}
    linked_classification_job_id UUID REFERENCES classification_jobs(id) ON DELETE SET NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL        -- TTL: created_at + OCR_RESULT_TTL_HOURS
);

CREATE INDEX idx_ocr_jobs_status ON ocr_jobs(status);
CREATE INDEX idx_ocr_jobs_expires ON ocr_jobs(expires_at);
CREATE INDEX idx_ocr_jobs_sha ON ocr_jobs(file_sha256);
```

**Файлы:**
- Сами файлы **не** храним долгосрочно. Идея: после успешного OCR файл можно удалить (результат живёт в `result.ocr_data`).
- Для idempotency: при загрузке считаем `sha256` файла — если есть `completed`-job с тем же хэшем, не моложе X часов, возвращаем тот же `ocr_job_id` без повторного OCR (экономия денег и времени).
- Хранилище файлов на время обработки: `data/ocr_uploads/{ocr_job_id}` или S3-совместимое (см. §8).

**Миграция:** `migrations/versions/007_ocr_jobs.py`.

## 6. Конфигурация (env)

| Переменная | Описание | Default |
|---|---|---|
| `OCR_PROVIDER_BASE_URL` | URL OCR-провайдера | — (обязательно) |
| `OCR_PROVIDER_API_KEY` | API-ключ | — (обязательно) |
| `OCR_PROVIDER_TIMEOUT_S` | Timeout одного HTTP-запроса к провайдеру | `30` |
| `OCR_POLL_MAX_WAIT_S` | Макс. время ожидания результата | `600` |
| `OCR_POLL_INITIAL_INTERVAL_S` | Стартовый интервал backoff | `1.0` |
| `OCR_POLL_MAX_INTERVAL_S` | Потолок backoff | `10.0` |
| `OCR_MAX_FILE_MB` | Лимит размера файла | `25` |
| `OCR_RESULT_TTL_HOURS` | TTL хранения результата OCR | `72` |
| `OCR_DEDUP_WINDOW_HOURS` | Окно дедупликации по sha256 | `24` |
| `OCR_UPLOAD_DIR` | Локальная директория для временных файлов | `data/ocr_uploads` |
| `LEVEL_OCR_SPLIT_MODEL` | Override модели для splitter (level=98) | fallback на medium |

Provider URL и ключ — **обязательные**. Без них модуль OCR в DI-контейнере не регистрируется, эндпоинты возвращают 503.

## 7. Обработка ошибок

| Ситуация | Что делаем |
|---|---|
| Файл превышает лимит | 413 Payload Too Large сразу, без создания job |
| Неподдерживаемый MIME | 415 Unsupported Media Type |
| Провайдер вернул 4xx | `OcrJob.status='failed'`, в `error` — текст; не retry |
| Провайдер вернул 5xx | retry до 3 раз с backoff; затем `failed` |
| `poll` вышел за `max_wait` | `failed` с error="OCR timeout"; provider task НЕ отменяется (потенциально для аудита) |
| OcrSplitter LLM-ошибка | retry 1 раз; затем `failed` (но raw OCR-данные сохранены — фронтенд может показать как fallback) |
| Невалидный документ (страница пустая, не текст) | provider скажет → конкретный error message; статус `failed` |

**Принцип:** ошибка одного этапа не теряет результат предыдущего. Если OCR прошёл, но splitter упал — `result.ocr_data` уже сохранены, фронтенд показывает структурированные данные и предлагает «попробовать извлечь товары снова» (отдельный POST на retry-эндпоинт — см. §9).

## 8. Хранение файлов

**MVP:** локальная файловая система (`data/ocr_uploads/{job_id}.{ext}`). Удаление после `status in ('completed', 'failed')`. Для одного инстанса достаточно.

**Production:** S3-совместимое хранилище (MinIO для on-premise, AWS S3, Yandex Object Storage). Абстракция:

```python
class FileStorage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> str: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
```

С реализациями `LocalFileStorage` и `S3FileStorage`. DI выбирает по env `FILE_STORAGE_BACKEND` (local|s3).

Для multi-instance бэкенда — обязательно S3, иначе разные воркеры не увидят файл друг друга.

## 9. Retry и идемпотентность

- **Дедупликация при загрузке:** см. §5 (`file_sha256`).
- **Retry splitter:** `POST /api/v1/ocr/jobs/{id}/retry-extract` — повторный запуск splitter без повторного OCR. Полезно, если LLM упал или мы обновили промпт.
- **Webhook от провайдера** *(опционально):* если провайдер поддерживает callback вместо polling — можно сэкономить запросы. Реализация: `POST /api/v1/ocr/webhook` + проверка подписи. Polling в этом случае становится fallback при пропущенном callback.

## 10. Тесты

| Уровень | Что покрываем |
|---|---|
| unit (`tests/ocr/test_client.py`) | mock httpx; submit, poll с разными ответами, backoff |
| unit (`tests/ocr/test_splitter.py`) | заранее заготовленные OCR-результаты → проверка SplitterOutput |
| integration (`tests/ocr/test_service.py`) | весь pipeline с mock-провайдером |
| e2e (опционально, `tests/ocr/test_endpoints.py`) | реальный POST → проверка структуры ответа |

**Фикстуры:** заранее сохранённые JSON-ответы провайдера в `tests/fixtures/ocr/` (по одной на тип документа + edge cases: пустой инвойс, инвойс на 100 строк, многоязычный).

## 11. Безопасность

- **Антивирусная проверка** загружаемых файлов *(P2)*: ClamAV (локально) или провайдерский endpoint.
- **Проверка MIME-типа** не доверять только заголовку — проверять magic bytes (`python-magic`).
- **Изоляция:** не открывать PDF локально (никаких `pdfplumber` от пользовательского входа без sandbox) — провайдер сам всё парсит.
- **Логи:** не логируем сырое содержимое документа (PII), только метаданные (размер, тип, sha256, имя файла).

## 12. Метрики

Минимум:
- `ocr_jobs_total{status}` — counter;
- `ocr_duration_seconds{stage}` — histogram (upload, ocr, extract);
- `ocr_provider_errors_total{code}` — counter;
- `ocr_file_size_bytes` — histogram;
- `ocr_dedup_hits_total` — counter (сколько раз сработала идемпотентность).

## 13. Открытые вопросы

1. **Провайдер.** Точное имя/контракт API провайдера зафиксировать в этом документе по факту выбора (API-документация → §3.4 уточнить точные поля).
2. **Языки.** Поддерживает ли провайдер русский, кыргызский, китайский (для импорта из КНР)? Если нет — нужен пред-перевод?
3. **Скан качества.** Какие минимальные DPI/качество принимает провайдер? Что делать с откровенно плохими сканами (отказывать или помечать low-confidence)?
4. **PII.** Документы могут содержать персональные данные сторон сделки. Согласие на отправку внешнему OCR-провайдеру и retention-политика провайдера — оформить в `docs/data_processing.md`.
5. **Цена.** Bill-policy (тарификация на странице/документе/токене провайдера) — для счётчика стоимости в админке.

## 14. Roadmap

- **MVP (Sprint 1):** OcrClient + InvoiceOcrResult + OcrSplitter + 1 endpoint POST/GET + локальное хранилище.
- **Sprint 2:** Остальные DocumentType, дедупликация, retry-extract.
- **Sprint 3:** S3-storage, webhook, метрики.
- **Sprint 4:** Антивирус, multi-language pre-detection.
