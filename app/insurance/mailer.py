"""Outgoing email through the configured SMTP server."""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from app.core.config import settings


class EmailDeliveryError(Exception):
    pass


def _send(message: EmailMessage) -> None:
    try:
        with smtplib.SMTP(settings.smtp_host, int(settings.smtp_port), timeout=30) as client:
            if settings.smtp_use_tls:
                client.starttls()
            if settings.smtp_username and settings.smtp_password:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailDeliveryError(f"{type(exc).__name__}: {exc}"[:2000]) from exc


async def send(message: EmailMessage) -> None:
    """Send without blocking the event loop. Raises EmailDeliveryError on failure."""
    await asyncio.to_thread(_send, message)
