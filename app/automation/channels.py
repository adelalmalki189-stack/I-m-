"""Delivery channels for customer messages.

`sandbox` records messages as `simulated` without contacting any provider — the default
until real credentials exist. Live channels:

* WhatsApp Business Cloud API (Meta). Messages a business starts must use a template
  approved by Meta; set the template name per rule in Settings. Without a template name
  the rule's text is sent as a plain text message, which Meta only delivers inside the
  24-hour window after the customer last wrote to you.
* Unifonic SMS (Saudi provider). The sender ID must be registered with CST.

Request formats follow each provider's public REST API; confirm them against your
account's documentation when activating live mode.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from app.core.config import settings


class DeliveryError(Exception):
    """Provider refused or could not be reached. `retryable` False = don't try again."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Delivery:
    provider_message_id: str | None
    simulated: bool = False


def _intl(phone: str) -> str:
    """+9665XXXXXXXX → 9665XXXXXXXX (both providers want digits only)."""
    return phone.lstrip("+")


def _raise_for(response: httpx.Response, provider: str) -> None:
    if response.status_code < 300:
        return
    # 4xx (bad number, unapproved template, auth) won't fix itself; 429/5xx might.
    retryable = response.status_code == 429 or response.status_code >= 500
    raise DeliveryError(f"{provider} HTTP {response.status_code}: {response.text[:500]}", retryable)


async def send_whatsapp(
    client: httpx.AsyncClient, phone: str, body: str, template: str | None, params: list[str]
) -> Delivery:
    if not (settings.whatsapp_token and settings.whatsapp_phone_number_id):
        raise DeliveryError("WhatsApp credentials are not configured", retryable=False)
    url = (
        f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}/messages"
    )
    payload: dict = {"messaging_product": "whatsapp", "to": _intl(phone)}
    if template:
        payload |= {
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": settings.whatsapp_template_language},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in params]}],
            },
        }
    else:
        payload |= {"type": "text", "text": {"body": body}}
    response = await client.post(
        url, json=payload, headers={"Authorization": f"Bearer {settings.whatsapp_token}"}, timeout=20
    )
    _raise_for(response, "WhatsApp")
    messages = response.json().get("messages") or [{}]
    return Delivery(messages[0].get("id"))


async def send_sms(client: httpx.AsyncClient, phone: str, body: str) -> Delivery:
    if not (settings.unifonic_app_sid and settings.unifonic_sender_id):
        raise DeliveryError("Unifonic credentials are not configured", retryable=False)
    response = await client.post(
        settings.unifonic_api_url,
        data={
            "AppSid": settings.unifonic_app_sid,
            "SenderID": settings.unifonic_sender_id,
            "Recipient": _intl(phone),
            "Body": body,
        },
        timeout=20,
    )
    _raise_for(response, "Unifonic")
    data = response.json()
    if data.get("success") is False:
        raise DeliveryError(f"Unifonic: {json.dumps(data, ensure_ascii=False)[:500]}", retryable=False)
    return Delivery(str((data.get("data") or {}).get("MessageID") or "") or None)


def configured_channels() -> dict[str, bool]:
    return {
        "whatsapp": bool(settings.whatsapp_token and settings.whatsapp_phone_number_id),
        "sms": bool(settings.unifonic_app_sid and settings.unifonic_sender_id),
    }
