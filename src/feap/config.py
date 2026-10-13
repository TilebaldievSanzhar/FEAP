from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # --- Database ---
    DATABASE_URL: str = "postgresql+asyncpg://feap:feap@localhost:5432/feap"

    # --- OpenRouter ---
    OPENROUTER_API_KEY: str = ""
    MODEL_LIGHT: str = "anthropic/claude-haiku-4-5-20251001"
    MODEL_MEDIUM: str = "anthropic/claude-sonnet-4-6"
    MODEL_HEAVY: str = "anthropic/claude-opus-4-6"

    # --- Voyage AI ---
    VOYAGE_API_KEY: str = ""
    VOYAGE_MODEL: str = "voyage-3"
    VOYAGE_EMBEDDING_DIM: int = 1024

    # --- Per-level model overrides (optional) ---
    # Full OpenRouter model ID, e.g. "google/gemini-2.5-pro"
    # If not set, falls back to tier-based model selection.
    LEVEL0_MODEL: str = ""
    LEVEL1_MODEL: str = ""
    LEVEL2_MODEL: str = ""
    LEVEL3_MODEL: str = ""
    LEVEL4_MODEL: str = ""
    LEVEL5_MODEL: str = ""
    LEVEL90_MODEL: str = ""  # DT: типизация документа
    LEVEL91_MODEL: str = ""  # DT: извлечение полей документа
    LEVEL92_MODEL: str = ""  # DT: кросс-сверка (сопоставление сущностей)
    LEVEL93_MODEL: str = ""  # DT: генератор графы 31
    LEVEL94_MODEL: str = ""  # DT: риск-советник
    LEVEL95_MODEL: str = ""  # DT: findings-fixer
    LEVEL98_MODEL: str = ""  # OCR raw-text → ProductCard[] structurer
    LEVEL99_MODEL: str = ""  # NTM verification

    # --- Cascade tuning ---
    CASCADE_MAX_BACKTRACK: int = 5
    SGR_EVIDENCE_POLICY: Literal['legacy', 'best_guess', 'clarify'] = 'legacy'
    SESSION_TTL_MINUTES: int = 30
    SESSION_MAX_ANSWERS: int = 3

    # --- Tax (Kyrgyzstan) ---
    VAT_RATE_DEFAULT: str = "12%"

    # --- Auth (Track B1) ---
    # optional: токен проверяется, если прислан; без него — X-Org-Id/'default'
    #           (dev-режим, поведение до B1 полностью сохраняется).
    # required: все защищённые эндпоинты требуют Bearer-токен.
    AUTH_MODE: str = "optional"
    # Секрет подписи JWT (HS256). Пусто в optional-режиме → dev-секрет
    # (предсказуемый, только для локалки); в required — старт падает.
    AUTH_SECRET: str = ""
    AUTH_TOKEN_TTL_MINUTES: int = 1440  # 24 часа

    # --- Redis / job queue ---
    REDIS_URL: str = "redis://redis:6379/0"
    JOBS_BACKEND: str = "arq"  # arq | inprocess

    # --- Worker (arq) ---
    WORKER_REPLICAS: int = 2
    WORKER_MAX_JOBS: int = 16
    WORKER_JOB_TIMEOUT_S: int = 3600
    WORKER_MAX_TRIES: int = 1
    WORKER_HEALTH_CHECK_S: int = 30

    # --- LLM concurrency ---
    LLM_MAX_CONCURRENT: int = 8
    LLM_RATE_LIMIT_PER_MIN: int = 0  # 0 = unlimited
    LLM_RATE_LIMITER_BACKEND: str = "local"  # local | redis

    # --- Batch limits ---
    BATCH_SYNC_MAX: int = 5
    BATCH_ASYNC_MAX_ITEMS: int = 200

    # --- Job lifecycle / TTL ---
    JOB_RESULT_TTL_HOURS: int = 72
    JOB_EVENTS_RETENTION_HOURS: int = 24
    STUCK_ITEMS_RESET_AFTER_S: int = 900
    SSE_HEARTBEAT_S: int = 15

    # --- DT core (проект декларации на товары) ---
    # Сбор за таможенные операции (ПП КР № 79 от 13.02.2020, п. 38):
    #   percent — 0,4% от таможенной стоимости ДТ, не менее 5 и не более 2500 РП;
    #   fixed   — фиксированная сумма DT_CUSTOMS_FEE_KGS (ручной режим);
    #   off     — строка сбора не формируется (ФЛК: info W-FEE).
    DT_CUSTOMS_FEE_MODE: Literal["percent", "fixed", "off"] = "percent"
    DT_CUSTOMS_FEE_KGS: str = ""
    # Расчётный показатель КР, сом (база минимума/максимума сбора).
    DT_CALC_INDEX_KGS: str = "100"
    # База НДС на импорт (ст. 310 НК КР) — включать ли антидемпинговые,
    # специальные и компенсационные пошлины. Норма прямо их не называет;
    # ФЛК всегда предупреждает (W-REMEDY-VAT), когда такие пошлины есть.
    DT_VAT_INCLUDE_REMEDY_DUTIES: bool = False
    # Официальные курсы НБКР (ежедневный XML)
    NBKR_RATES_URL: str = "https://www.nbkr.kg/XML/daily.xml"
    # PDF-экспорт: путь к TTF с кириллицей. Пусто = автопоиск
    # (Arial на Windows, DejaVu Sans на Linux) — см. exporters/pdf.py.
    DT_PDF_FONT_PATH: str = ""
    DT_PDF_FONT_BOLD_PATH: str = ""

    # --- DT: форматы (Фаза 3) ---
    # Версия структуры XML-экспорта (пишется в атрибут structureVersion).
    # Финальный маппинг на альбом R.055 (Решение ЕЭК № 75) подтверждается
    # пилотом загрузки в АРМ «Брокер»/KEDEN — правки Решения выходят ежегодно.
    DT_XML_STRUCTURE_VERSION: str = "R.055-draft/1"
    # Путь к официальному XSD; пусто = валидация пропускается.
    # Требует пакет `xmlschema` (опциональный: pip install xmlschema).
    DT_XML_XSD_PATH: str = ""
    # --- TULPAR (проверка разрешительных, Фаза 3) ---
    # URL поиска по публичным реестрам swis2.trade.kg; пусто = HTTP-проверка
    # отключена (работает только детерминированная проверка формата номера).
    TULPAR_SEARCH_URL: str = ""
    TULPAR_TIMEOUT_S: int = 20
    TULPAR_CACHE_HOURS: int = 24

    # --- DT: SGR-слой (Фаза 2) ---
    # Типизация документа: ниже порога → kind='unknown', тип выбирает человек.
    DT_DOCTYPE_CONFIDENCE_MIN: float = 0.6
    # Кросс-сверка: сопоставление сущностей ниже порога = «не сопоставлено».
    DT_MATCH_CONFIDENCE_MIN: float = 0.6
    # Графа 31: сколько раз перегенерировать текст после отклонения
    # grounding-валидатором (аналог COMPLETION_MAX_REJECTIONS).
    DT_G31_MAX_REJECTIONS: int = 2
    # Риск-сигнал «цена/кг»: во сколько раз отклонение от медианы досье
    # считается выбросом.
    DT_PRICE_OUTLIER_FACTOR: float = 4.0

    # --- OCR provider (ocr.trade.kg internal service) ---
    OCR_PROVIDER_URL: str = "http://192.168.250.44:8088/ocr/process"
    OCR_PROVIDER_TIMEOUT_S: int = 300
    OCR_MAX_FILE_MB: int = 25
    OCR_MAX_FILES_PER_JOB: int = 10
    OCR_RESULT_TTL_HOURS: int = 72
    OCR_UPLOAD_DIR: str = "data/ocr_uploads"
    OCR_ALLOWED_MIME: str = "application/pdf,image/jpeg,image/png,image/tiff,image/webp"

    # Sync URL for Alembic / scripts
    @property
    def database_url_sync(self) -> str:
        return self.DATABASE_URL.replace("+asyncpg", "+psycopg2")


settings = Settings()
