import logging
from functools import lru_cache
from typing import Literal
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    BOT_MODE: Literal["polling", "webhook"] = "polling"
    ENVIRONMENT: Literal["development", "production"] = "development"
    LOG_LEVEL: str = "INFO"

    TELEGRAM_BOT_TOKEN: str = "placeholder_token_change_in_env"
    WEBHOOK_URL: str = ""
    WEBHOOK_SECRET_TOKEN: str = ""

    DATABASE_URL: str = "sqlite+aiosqlite:///./amuecsf.db"
    LEADER_IDS: list[int] | str = []
    CRISIS_ALERT_CHAT_ID: int | str | None = None

    @field_validator("LEADER_IDS", mode="after")
    @classmethod
    def parse_leader_ids(cls, value: str | list[int] | int | None) -> list[int]:
        if value is None or value == "":
            return []
        if isinstance(value, int):
            return [value]
        if isinstance(value, list):
            return [int(x) for x in value]
        raw = str(value).strip()
        if raw.startswith("[") and raw.endswith("]"):
            import json
            return [int(x) for x in json.loads(raw)]
        return [int(item.strip()) for item in raw.split(",") if item.strip()]

    @field_validator("CRISIS_ALERT_CHAT_ID", mode="after")
    @classmethod
    def parse_crisis_chat_id(cls, value: str | int | None) -> int | None:
        if value is None or str(value).strip() == "":
            return None
        return int(value)

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        url = str(value).strip()
        # Fallback to local SQLite if placeholder from .env.example is left unchanged
        if "@host/dbname" in url or "user:password@host" in url:
            logger.warning(
                "Placeholder DATABASE_URL detected ('@host/dbname'). "
                "Defaulting to local SQLite 'sqlite+aiosqlite:///./amuecsf.db' for local development."
            )
            return "sqlite+aiosqlite:///./amuecsf.db"

        # Ensure asyncpg driver and compatible SSL query params for SQLAlchemy + Neon
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
