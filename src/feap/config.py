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
    LEVEL99_MODEL: str = ""  # NTM verification

    # --- Cascade tuning ---
    CASCADE_MAX_BACKTRACK: int = 5
    SESSION_TTL_MINUTES: int = 30
    SESSION_MAX_ANSWERS: int = 3

    # --- Tax (Kyrgyzstan) ---
    VAT_RATE_DEFAULT: str = "12%"

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

    # Sync URL for Alembic / scripts
    @property
    def database_url_sync(self) -> str:
        return self.DATABASE_URL.replace("+asyncpg", "")


settings = Settings()
