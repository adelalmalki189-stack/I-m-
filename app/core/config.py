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

    # Staff sessions. Set SESSION_COOKIE_SECURE=true whenever the app is served over HTTPS.
    session_hours: int = 12
    session_cookie_secure: bool = False
    login_max_attempts: int = 5
    login_lock_minutes: int = 15
    # Public portal abuse limits (per client IP, per hour).
    portal_requests_per_hour: int = 10
    portal_max_upload_mb: int = 10

    # Customer messaging. Credentials only come from the environment; the manager chooses
    # sandbox/live and the channel in Settings → Automation.
    whatsapp_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_api_version: str = "v21.0"
    whatsapp_template_language: str = "ar"
    unifonic_app_sid: str | None = None
    unifonic_sender_id: str | None = None
    unifonic_api_url: str = "https://el.cloud.unifonic.com/rest/SMS/messages"


settings = Settings()
