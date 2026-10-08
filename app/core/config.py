from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "StockPilotAI"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    database_url: str = "sqlite:///data/stockpilot.db"

    openai_api_key: SecretStr | None = None
    openai_default_model: str = "gpt-6.1-sol"
    openai_allowed_models: str = "gpt-6.1-sol,gpt-6-luna,gpt-5-mini"
    openai_timeout_seconds: float = Field(default=60, gt=0, le=300)
    market_data_timeout_seconds: float = Field(default=20, gt=0, le=120)

    @field_validator("database_url", "openai_default_model")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @property
    def allowed_models(self) -> tuple[str, ...]:
        models = tuple(
            dict.fromkeys(
                model.strip()
                for model in self.openai_allowed_models.split(",")
                if model.strip()
            )
        )
        if self.openai_default_model not in models:
            return (self.openai_default_model, *models)
        return models


@lru_cache
def get_settings() -> Settings:
    return Settings()
