"""Queue customer messages and deliver them."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation import channels
from app.automation.models import AutomationSetting, CustomerNotification, NotificationStatus
from app.automation.rules import RULES, get_rule, render
from app.insurance.models import Customer, InsuranceRequest

MODES = ("sandbox", "live")
CHANNELS = ("whatsapp", "sms")
DEFAULTS = {"messaging_mode": "sandbox", "customer_channel": "whatsapp"}
MAX_ATTEMPTS = 5
# Messages the customer asked for themselves (e.g. a verification code) ignore the opt-out.
TRANSACTIONAL = {"otp"}
BACKOFF = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2)]


async def get_setting(session: AsyncSession, key: str) -> str:
    row = await session.get(AutomationSetting, key)
    return row.value if row else DEFAULTS[key]


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(AutomationSetting, key)
    if row is None:
        session.add(AutomationSetting(key=key, value=value))
    else:
        row.value = value


async def enqueue(
    session: AsyncSession,
    kind: str,
    customer: Customer,
    context: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    request_id: str | None = None,
    policy_id: str | None = None,
    claim_id: str | None = None,
    body: str | None = None,
    created_by: str | None = None,
) -> CustomerNotification | None:
    """Add a message to the outbox (committed with the caller's transaction).

    `kind` is a rule key from RULES, or "manual"/"otp" with an explicit `body`. Returns None when
    the rule is switched off or the same `dedupe_key` was already queued.
    """
    if dedupe_key:
        existing = await session.execute(
            select(CustomerNotification.notification_id).where(CustomerNotification.dedupe_key == dedupe_key)
        )
        if existing.first() is not None:
            return None

    template: str | None = None
    params: list[str] = []
    if kind not in ("manual", "otp"):
        rule = await get_rule(session, kind)
        if not rule.enabled or rule.message is None:
            return None
        body = render(rule.message, context)
        template = rule.whatsapp_template
        params = [str(context.get(v, "")) for v in RULES[kind].variables]
    if not body:
        raise ValueError("A message body is required")

    phone = await recipient(session, customer, request_id)
    status, error = NotificationStatus.QUEUED, None
    if customer.messaging_opt_out and kind not in TRANSACTIONAL:
        status, error = NotificationStatus.SKIPPED, "العميل ألغى الاشتراك في الرسائل"
    elif not phone:
        status, error = NotificationStatus.SKIPPED, "لا يوجد رقم جوال للعميل"

    notification = CustomerNotification(
        customer_id=customer.customer_id,
        kind=kind,
        channel=await get_setting(session, "customer_channel"),
        to_phone=phone,
        body=body,
        template_params=json.dumps({"template": template, "params": params}, ensure_ascii=False)
        if template
        else None,
        status=status,
        last_error=error,
        dedupe_key=dedupe_key,
        request_id=request_id,
        policy_id=policy_id,
        claim_id=claim_id,
        created_by=created_by,
    )
    session.add(notification)
    await session.flush()
    return notification


async def _deliver(client: httpx.AsyncClient, n: CustomerNotification) -> channels.Delivery:
    assert n.to_phone
    if n.channel == "whatsapp":
        spec = json.loads(n.template_params) if n.template_params else {}
        return await channels.send_whatsapp(
            client, n.to_phone, n.body, spec.get("template"), spec.get("params", [])
        )
    return await channels.send_sms(client, n.to_phone, n.body)


async def recipient(session: AsyncSession, customer: Customer, request_id: str | None) -> str | None:
    """A request's messages go to whoever filed it for the business; others to the customer's
    number. Read again at delivery, so a corrected number is used."""
    request = await session.get(InsuranceRequest, request_id) if request_id else None
    return (request.contact_mobile if request else None) or customer.phone


async def _send_one(
    client: httpx.AsyncClient,
    n: CustomerNotification,
    customer: Customer | None,
    phone: str | None,
    mode: str,
    now: datetime,
) -> str:
    """Try to deliver one message; returns the outcome key (sent/simulated/retry/failed/skipped)."""
    opted_out = customer is not None and customer.messaging_opt_out and n.kind not in TRANSACTIONAL
    if customer is None or opted_out or not phone:
        n.status, n.last_error = NotificationStatus.SKIPPED, "العميل ألغى الاشتراك أو لا يوجد رقم"
        return "skipped"
    n.to_phone = phone
    n.attempts += 1
    if mode != "live":
        n.status, n.sent_at = NotificationStatus.SIMULATED, now
        return "simulated"
    try:
        delivery = await _deliver(client, n)
    except (channels.DeliveryError, httpx.HTTPError) as exc:
        n.last_error = str(exc)[:2000]
        if getattr(exc, "retryable", True) and n.attempts < MAX_ATTEMPTS:
            n.next_attempt_at = now + BACKOFF[min(n.attempts - 1, len(BACKOFF) - 1)]
            return "retry"
        n.status = NotificationStatus.FAILED
        return "failed"
    n.status, n.sent_at = NotificationStatus.SENT, now
    n.provider_message_id, n.last_error = delivery.provider_message_id, None
    return "sent"


async def deliver_now(
    session: AsyncSession, n: CustomerNotification, client: httpx.AsyncClient | None = None
) -> str:
    """Send one message immediately (verification codes can't wait for the worker cycle)."""
    customer = await session.get(Customer, n.customer_id)
    phone = await recipient(session, customer, n.request_id) if customer else None
    mode = await get_setting(session, "messaging_mode")
    if client is not None:
        return await _send_one(client, n, customer, phone, mode, datetime.now(UTC))
    async with httpx.AsyncClient() as own:
        return await _send_one(own, n, customer, phone, mode, datetime.now(UTC))


async def dispatch_outbox(
    session: AsyncSession, client: httpx.AsyncClient | None = None, now: datetime | None = None, limit: int = 50
) -> dict[str, int]:
    """Deliver due queued messages. Sandbox mode marks them `simulated` without sending."""
    now = now or datetime.now(UTC)
    result = await session.execute(
        select(CustomerNotification)
        .where(CustomerNotification.status == NotificationStatus.QUEUED)
        .where(or_(CustomerNotification.next_attempt_at.is_(None), CustomerNotification.next_attempt_at <= now))
        .order_by(CustomerNotification.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    batch = result.scalars().all()
    counts = {"sent": 0, "simulated": 0, "retry": 0, "failed": 0, "skipped": 0}
    if not batch:
        return counts
    mode = await get_setting(session, "messaging_mode")
    customers = {
        c.customer_id: c
        for c in (
            await session.execute(select(Customer).where(Customer.customer_id.in_({n.customer_id for n in batch})))
        ).scalars()
    }
    own_client = client is None
    client = client or httpx.AsyncClient()
    try:
        for n in batch:
            customer = customers.get(n.customer_id)
            phone = await recipient(session, customer, n.request_id) if customer else None
            counts[await _send_one(client, n, customer, phone, mode, now)] += 1
    finally:
        if own_client:
            await client.aclose()
    await session.commit()
    return counts
