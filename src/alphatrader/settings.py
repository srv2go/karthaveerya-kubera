"""Application settings, loaded from environment variables / .env.

No secrets are ever logged or included in exceptions raised from this module.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = Field(default="changeme")
    telegram_chat_id: str = Field(default="changeme")

    llm_provider: str = Field(default="anthropic")
    llm_api_key: str = Field(default="changeme")
    llm_base_url: str = Field(default="https://openrouter.ai/api/v1")
    llm_model: str = Field(default="claude-opus-4-5-20251101")

    data_source: str = Field(default="alpaca")
    alpaca_key_id: str = Field(default="changeme")
    alpaca_secret: str = Field(default="changeme")

    alphatrader_db_path: str = Field(default="alphatrader.db")
    alphatrader_risk_config: str = Field(default="config/risk.yaml")
    alphatrader_symbols_config: str = Field(default="config/symbols.yaml")

    def redacted(self) -> dict:
        """Return a dict of settings safe to log (secrets masked)."""
        data = self.model_dump()
        for key in ("telegram_bot_token", "llm_api_key", "alpaca_key_id", "alpaca_secret"):
            if data.get(key) and data[key] != "changeme":
                data[key] = "***redacted***"
        return data

    @property
    def risk_config_path(self) -> Path:
        return Path(self.alphatrader_risk_config)

    @property
    def symbols_config_path(self) -> Path:
        return Path(self.alphatrader_symbols_config)

    @property
    def db_path(self) -> Path:
        return Path(self.alphatrader_db_path)


def get_settings() -> Settings:
    return Settings()
