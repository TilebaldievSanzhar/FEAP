from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    DATABASE_URL: str = "postgresql+asyncpg://feap:feap@localhost:5432/feap"
    ANTHROPIC_API_KEY: str = ""
    VOYAGE_API_KEY: str = ""

    # Sync URL for Alembic / scripts
    @property
    def database_url_sync(self) -> str:
        return self.DATABASE_URL.replace("+asyncpg", "")


settings = Settings()
