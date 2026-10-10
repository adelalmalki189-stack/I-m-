"""Application settings.

`environment` drives the production DataProvider allow-list guard described
in docs/TESTING_SPEC_V0.1.md §2: FakeDataProvider must never be reachable
when environment == "production".
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    environment: str = "development"  # "development" | "test" | "production"

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/options_trading"
    redis_url: str = "redis://localhost:6379/0"

    email_delivery_enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str = "noreply@broker.local"
    smtp_use_tls: bool = True

    # IBKR TWS/Gateway connection (Paper account per docs/IMPLEMENTATION_PLAN_V0.1.md Phase 0)
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497  # 7497 = TWS paper trading, 4002 = IB Gateway paper
    ibkr_client_id: int = 1

    # docs/OPERATIONAL_SPEC_V0.1.md §2 — data staleness thresholds (seconds)
    underlying_quote_stale_seconds: float = 5.0
    option_quote_stale_seconds: float = 10.0
    candle_stale_seconds: float = 15.0
    market_context_stale_seconds: float = 10.0


settings = Settings()
