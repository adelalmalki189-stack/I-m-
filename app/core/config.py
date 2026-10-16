"""Application settings, read from environment variables / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    environment: str = "development"  # "development" | "test" | "production"
    sentry_dsn: str | None = None  # error tracking; empty = off
    public_base_url: str = "http://localhost:8000"  # used in links sent to customers
    # Name printed on the customer's authorization letter: put the licensed legal name here.
    broker_legal_name: str = "بُعد للوساطة التأمينية"
    authorization_valid_days: int = 90
    # Where customers transfer the premium (printed on payment requests). Empty IBAN = the
    # request asks the customer to contact BUD for payment details.
    payment_bank_name: str = ""
    payment_account_name: str = ""
    payment_iban: str = ""
    payment_due_days: int = 7
    # Optional online payment page; {reference} and {amount} are filled in.
    payment_link_template: str = ""

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
    # Testing only: pick a staff member on the login page and enter without a password.
    # Never honoured when ENVIRONMENT=production.
    dev_login_enabled: bool = False
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

    # Insurer replies: the quotes mailbox read over IMAP (SSL). Empty host = feature off.
    imap_host: str | None = None
    imap_port: int = 993
    imap_username: str | None = None
    imap_password: str | None = None
    imap_folder: str = "INBOX"
    # AI quote extraction sends the insurer's email and attachments (which can contain
    # customer data) to the Anthropic API. Off unless explicitly enabled.
    ai_quote_extraction_enabled: bool = False
    ai_model: str = "claude-opus-5-5"


settings = Settings()
