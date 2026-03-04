from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # --- Database ---
    DATABASE_URL: str = "postgresql+asyncpg://feap:feap@localhost:5432/feap"

    # --- LLM Provider ---
    LLM_PROVIDER: str = "anthropic"  # "anthropic" | "google"

    # --- Anthropic ---
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL_LIGHT: str = "claude-haiku-4-5-20251001"
    ANTHROPIC_MODEL_MEDIUM: str = "claude-sonnet-4-6"
    ANTHROPIC_MODEL_HEAVY: str = "claude-opus-4-6"

    # --- Google Gemini ---
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL_LIGHT: str = "gemini-2.5-flash-lite"
    GEMINI_MODEL_MEDIUM: str = "gemini-2.5-flash"
    GEMINI_MODEL_HEAVY: str = "gemini-2.5-pro"

    # --- Voyage AI ---
    VOYAGE_API_KEY: str = ""
    VOYAGE_MODEL: str = "voyage-3"
    VOYAGE_EMBEDDING_DIM: int = 1024

    # --- Cascade tuning ---
    CASCADE_MAX_BACKTRACK: int = 3
    SESSION_TTL_MINUTES: int = 30
    SESSION_MAX_ANSWERS: int = 3

    # --- Tax (Kyrgyzstan) ---
    VAT_RATE_DEFAULT: str = "12%"

    # Sync URL for Alembic / scripts
    @property
    def database_url_sync(self) -> str:
        return self.DATABASE_URL.replace("+asyncpg", "")


settings = Settings()
