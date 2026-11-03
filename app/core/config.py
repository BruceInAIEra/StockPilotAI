from __future__ import annotations

from functools import lru_cache

from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
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

    app_base_url: str = "http://localhost:8000"
    cognito_region: str = "us-west-2"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_client_secret: SecretStr | None = None
    cognito_domain: str = ""
    session_encryption_key: SecretStr | None = None
    session_lifetime_seconds: int = Field(default=3600, ge=300, le=3600)

    @model_validator(mode="after")
    def validate_auth(self):
        base = urlsplit(self.app_base_url)
        if (base.scheme not in {"http", "https"} or not base.hostname
                or base.username or base.password or base.query or base.fragment
                or base.path not in {"", "/"}):
            raise ValueError("APP_BASE_URL must be an origin without a path")
        if base.scheme == "http" and base.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("APP_BASE_URL requires HTTPS except on localhost")
        self.app_base_url = self.app_base_url.rstrip("/")
        if self.cognito_region != "us-west-2":
            raise ValueError("Cognito must use the selected Region us-west-2")
        if self.cognito_user_pool_id and not self.cognito_user_pool_id.startswith("us-west-2_"):
            raise ValueError("Cognito user pool must be in us-west-2")
        if self.cognito_domain:
            domain = urlsplit(self.cognito_domain)
            if (domain.scheme != "https" or not domain.hostname or domain.username
                    or domain.password or domain.query or domain.fragment
                    or domain.path not in {"", "/"}):
                raise ValueError("COGNITO_DOMAIN must be an HTTPS origin")
            self.cognito_domain = self.cognito_domain.rstrip("/")
        if self.session_encryption_key:
            from cryptography.fernet import Fernet
            Fernet(self.session_encryption_key.get_secret_value().encode())
        return self

    @property
    def auth_configured(self) -> bool:
        return bool(self.cognito_user_pool_id and self.cognito_client_id
                    and self.cognito_domain and self.session_encryption_key)

    @property
    def secure_cookies(self) -> bool:
        return self.app_base_url.startswith("https://")

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"

    openai_api_key: SecretStr | None = None
    openai_default_model: str = "gpt-6.1-sol"
    openai_allowed_models: str = "gpt-6.1-sol,gpt-6-luna,gpt-5-mini"
    openai_timeout_seconds: float = Field(default=180, gt=0, le=600)
    openai_max_retries: int = Field(default=1, ge=0, le=2)
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
