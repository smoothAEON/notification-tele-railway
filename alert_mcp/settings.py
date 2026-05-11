"""Validated runtime settings for the OANDA Alert MCP service."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Configuration loaded from `.env` and process environment."""

    model_config = SettingsConfigDict(
        env_file=str(DEFAULT_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    oanda_api_key: SecretStr = Field(validation_alias="OANDA_API_KEY")
    oanda_account_id: SecretStr = Field(validation_alias="OANDA_ACCOUNT_ID")
    oanda_environment: Literal["practice", "live"] = Field(validation_alias="OANDA_ENVIRONMENT")

    telegram_bot_token: SecretStr = Field(validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(validation_alias="TELEGRAM_CHAT_ID")
    mcp_http_api_key: SecretStr = Field(validation_alias="MCP_HTTP_API_KEY")

    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_json: bool = Field(default=False, validation_alias="LOG_JSON")
    mcp_http_host: str = Field(default="0.0.0.0", validation_alias="MCP_HTTP_HOST")
    mcp_http_port: int = Field(default=8000, gt=0, le=65535, validation_alias="MCP_HTTP_PORT")
    mcp_http_path: str = Field(default="/mcp", validation_alias="MCP_HTTP_PATH")
    alert_db_path: Path = Field(default=Path("/data/alerts.db"), validation_alias="ALERT_DB_PATH")
    stream_instruments: str = Field(default="", validation_alias="STREAM_INSTRUMENTS")
    price_cache_ttl_seconds: int = Field(default=30, gt=0, validation_alias="PRICE_CACHE_TTL_SECONDS")
    stream_reconnect_max_seconds: int = Field(default=60, gt=0, validation_alias="STREAM_RECONNECT_MAX_SECONDS")

    @field_validator("oanda_environment", mode="before")
    @classmethod
    def normalize_environment(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if value not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}.")
        return value

    @field_validator("telegram_chat_id", "mcp_http_host", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("telegram_chat_id", "mcp_http_host")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value:
            raise ValueError("value must be non-empty.")
        return value

    @field_validator("mcp_http_path", mode="before")
    @classmethod
    def normalize_mcp_path(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip() or "/mcp"
            if not text.startswith("/"):
                text = f"/{text}"
            return text.rstrip("/") or "/"
        return value

    @field_validator("stream_instruments", mode="before")
    @classmethod
    def parse_stream_instruments(cls, value: object) -> object:
        if value is None or value == "":
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (list, tuple, set)):
            return ",".join(str(part).strip() for part in value if str(part).strip())
        return value

    @field_validator("alert_db_path")
    @classmethod
    def resolve_alert_db_path(cls, value: Path) -> Path:
        candidate = value.expanduser()
        if candidate.is_absolute():
            return candidate
        return (REPO_ROOT / candidate).resolve()

    @property
    def effective_port(self) -> int:
        raw_port = os.getenv("PORT")
        if raw_port:
            try:
                return int(raw_port)
            except ValueError as exc:
                raise ValueError(f"Invalid PORT value: {raw_port}") from exc
        return self.mcp_http_port

    @property
    def stream_instrument_list(self) -> tuple[str, ...]:
        return tuple(part.strip() for part in self.stream_instruments.split(",") if part.strip())


def load_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
