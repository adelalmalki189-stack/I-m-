"""Application settings, read from environment variables / .env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    environment: str = "development"  # "development" | "test" | "production"

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/bud"

    email_delivery_enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str = "noreply@broker.local"
    smtp_use_tls: bool = True


settings = Settings()
