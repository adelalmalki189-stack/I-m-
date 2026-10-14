"""Automation settings, rules, the customer message log, and manual messages."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import MANAGERS, UNDERWRITING, audit, get_current_staff, require_roles
from app.api.deps import get_db_session
from app.automation import channels, jobs, service
from app.automation.models import AutomationRule, CustomerNotification, NotificationStatus
from app.automation.rules import RULES, get_rule, unknown_variables
from app.insurance.models import Customer, SalesEmployee

router = APIRouter(prefix="/insurance", tags=["automation"], dependencies=[Depends(get_current_staff)])


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message})


class RuleView(BaseModel):
    key: str
    label: str
    description: str
    enabled: bool
    sends_message: bool
    message: str | None
    default_message: str | None
    variables: list[str]
    whatsapp_template: str | None
    params: dict[str, Any]


class AutomationView(BaseModel):
    messaging_mode: str
    customer_channel: str
    configured_channels: dict[str, bool]
    rules: list[RuleView]


class SettingsUpdate(BaseModel):
    messaging_mode: str | None = Field(default=None, pattern="^(sandbox|live)$")
    customer_channel: str | None = Field(default=None, pattern="^(whatsapp|sms)$")


class RuleUpdate(BaseModel):
    enabled: bool | None = None
    message: str | None = Field(default=None, max_length=1000)  # "" resets to the built-in wording
    whatsapp_template: str | None = Field(default=None, max_length=120)
    params: dict[str, Any] | None = None


class NotificationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    notification_id: str
    customer_id: str
    customer_name: str = ""
    kind: str
    kind_label: str = ""
    channel: str
    to_phone: str | None
    body: str
    status: NotificationStatus
    attempts: int
    last_error: str | None
    sent_at: datetime | None
    created_at: datetime
    created_by: str | None
    request_id: str | None
    policy_id: str | None
    claim_id: str | None


class ManualMessage(BaseModel):
    text: str = Field(..., min_length=2, max_length=1000)


# Allowed numeric parameters per rule, so a typo can't break the scheduled jobs.
PARAM_RULES: dict[str, dict[str, tuple[int, int]]] = {
    "renewal_reminder": {"days": (1, 120)},
    "installment_due": {"days_before": (0, 30)},
    "installment_overdue": {"days_after": (1, 90)},
    "auto_renewal_request": {"days": (7, 120)},
    "insurer_followup": {"hours": (4, 240), "max_followups": (0, 5)},
}


def _validate_params(key: str, params: dict[str, Any]) -> dict[str, Any]:
    allowed = PARAM_RULES.get(key, {})
    clean: dict[str, Any] = {}
    for name, value in params.items():
        if name not in allowed:
            raise _error(422, "UNKNOWN_PARAM", f"'{name}' is not a setting of {key}")
        low, high = allowed[name]
        values = value if isinstance(value, list) else [value]
        try:
            numbers = [int(v) for v in values]
        except (TypeError, ValueError) as exc:
            raise _error(422, "INVALID_PARAM", f"{name} must be a whole number") from exc
        if not numbers or any(not low <= n <= high for n in numbers):
            raise _error(422, "INVALID_PARAM", f"{name} must be between {low} and {high}")
        clean[name] = sorted(set(numbers), reverse=True) if isinstance(value, list) else numbers[0]
    return clean


async def _view(session: AsyncSession) -> AutomationView:
    rules = []
    for key, base in RULES.items():
        rule = await get_rule(session, key)
        rules.append(
            RuleView(
                key=key,
                label=base.label,
                description=base.description,
                enabled=rule.enabled,
                sends_message=base.message is not None,
                message=rule.message,
                default_message=base.message,
                variables=list(base.variables),
                whatsapp_template=rule.whatsapp_template,
                params=rule.params,
            )
        )
    return AutomationView(
        messaging_mode=await service.get_setting(session, "messaging_mode"),
        customer_channel=await service.get_setting(session, "customer_channel"),
        configured_channels=channels.configured_channels(),
        rules=rules,
    )


@router.get("/automation", response_model=AutomationView)
async def get_automation(
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*MANAGERS)),
) -> AutomationView:
    return await _view(session)


@router.put("/automation/settings", response_model=AutomationView)
async def update_settings(
    body: SettingsUpdate,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*MANAGERS)),
) -> AutomationView:
    channel = body.customer_channel or await service.get_setting(session, "customer_channel")
    mode = body.messaging_mode or await service.get_setting(session, "messaging_mode")
    if mode == "live" and not channels.configured_channels()[channel]:
        raise _error(
            409, "CHANNEL_NOT_CONFIGURED", f"Add the {channel} credentials to the server before going live"
        )
    await service.set_setting(session, "messaging_mode", mode)
    await service.set_setting(session, "customer_channel", channel)
    audit(session, staff, "automation_settings_changed", "automation", None, mode=mode, channel=channel)
    await session.commit()
    return await _view(session)


@router.put("/automation/rules/{key}", response_model=AutomationView)
async def update_rule(
    key: str,
    body: RuleUpdate,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*MANAGERS)),
) -> AutomationView:
    if key not in RULES:
        raise _error(404, "RULE_NOT_FOUND", f"No automation rule '{key}'")
    row = await session.get(AutomationRule, key)
    if row is None:
        row = AutomationRule(key=key, enabled=True)
        session.add(row)
    changes = body.model_dump(exclude_unset=True)
    if "enabled" in changes:
        row.enabled = bool(body.enabled)
    if "message" in changes:
        text = (body.message or "").strip()
        if text and RULES[key].message is None:
            raise _error(422, "NO_MESSAGE", "This rule does not send a customer message")
        if text and (unknown := unknown_variables(key, text)):
            raise _error(422, "UNKNOWN_TEMPLATE_VARIABLES", f"Unknown variables: {', '.join(unknown)}")
        row.message = text or None
    if "whatsapp_template" in changes:
        row.whatsapp_template = (body.whatsapp_template or "").strip() or None
    if body.params is not None:
        current = json.loads(row.params) if row.params else {}
        row.params = json.dumps(current | _validate_params(key, body.params))
    audit(session, staff, "automation_rule_changed", "automation", None, rule=key, fields=sorted(changes))
    await session.commit()
    return await _view(session)


@router.post("/automation/run")
async def run_now(
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*MANAGERS)),
) -> dict[str, Any]:
    """Run the scheduled jobs and deliver the outbox immediately (the worker does this hourly)."""
    summary = await jobs.run_daily_jobs(session)
    summary["outbox"] = await service.dispatch_outbox(session)
    return summary


async def _notification_views(session: AsyncSession, rows: list[CustomerNotification]) -> list[NotificationView]:
    ids = {r.customer_id for r in rows}
    customers = (
        {
            c.customer_id: c
            for c in (await session.execute(select(Customer).where(Customer.customer_id.in_(ids)))).scalars()
        }
        if ids
        else {}
    )
    views = []
    for row in rows:
        view = NotificationView.model_validate(row)
        customer = customers.get(row.customer_id)
        view.customer_name = (customer.company_name or customer.full_name) if customer else ""
        view.kind_label = RULES[row.kind].label if row.kind in RULES else "رسالة يدوية"
        views.append(view)
    return views


@router.get("/notifications", response_model=list[NotificationView])
async def list_notifications(
    status: NotificationStatus | None = None,
    customer_id: str | None = None,
    limit: int = 200,
    session: AsyncSession = Depends(get_db_session),
) -> list[NotificationView]:
    stmt = (
        select(CustomerNotification)
        .order_by(CustomerNotification.created_at.desc())
        .limit(min(max(limit, 1), 500))
    )
    if status:
        stmt = stmt.where(CustomerNotification.status == status)
    if customer_id:
        stmt = stmt.where(CustomerNotification.customer_id == customer_id)
    return await _notification_views(session, list((await session.execute(stmt)).scalars()))


@router.post("/notifications/{notification_id}/retry", response_model=NotificationView)
async def retry_notification(
    notification_id: str,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> NotificationView:
    row = await session.get(CustomerNotification, notification_id)
    if row is None:
        raise _error(404, "NOTIFICATION_NOT_FOUND", "Message not found")
    if row.status not in (NotificationStatus.FAILED, NotificationStatus.SKIPPED):
        raise _error(409, "NOT_RETRYABLE", "Only failed or skipped messages can be resent")
    row.status, row.attempts, row.next_attempt_at, row.last_error = NotificationStatus.QUEUED, 0, None, None
    audit(session, staff, "notification_retried", "customer", row.customer_id, notification_id=notification_id)
    await session.commit()
    return (await _notification_views(session, [row]))[0]


@router.post("/customers/{customer_id}/messages", response_model=NotificationView, status_code=201)
async def send_manual_message(
    customer_id: str,
    body: ManualMessage,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> NotificationView:
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise _error(404, "CUSTOMER_NOT_FOUND", "Customer not found")
    row = await service.enqueue(
        session, "manual", customer, {}, body=body.text.strip(), created_by=staff.full_name
    )
    assert row is not None
    audit(session, staff, "manual_message", "customer", customer_id, at=datetime.now(UTC))
    await session.commit()
    return (await _notification_views(session, [row]))[0]
