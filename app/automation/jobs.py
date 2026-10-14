"""Scheduled automation jobs. Every job is idempotent: running it twice (or every hour)
never sends a reminder twice or opens two renewals — dedupe keys and status checks guard it.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import audit
from app.automation.rules import get_rule
from app.automation.service import enqueue
from app.core.config import settings
from app.insurance import aftersales, mailer
from app.insurance.models import (
    Customer,
    EmailAuditLog,
    InstallmentStatus,
    InsurancePolicy,
    InsurancePolicyStatus,
    InsuranceQuote,
    InsuranceRequest,
    InsuranceSubmission,
    InsuranceSubmissionStatus,
    Insurer,
    PremiumInstallment,
)

LIVE = (InsurancePolicyStatus.ISSUED, InsurancePolicyStatus.ACTIVE)
SYSTEM = "النظام الآلي"


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def money(value: Any) -> str:
    amount = aftersales.to_money(value)
    return f"{amount:,.2f} ر.س" if amount is not None else "—"


def customer_name(customer: Customer) -> str:
    return customer.company_name or customer.full_name


async def _policy_context(session: AsyncSession, policy: InsurancePolicy) -> tuple[Customer, dict[str, str]]:
    request = await session.get(InsuranceRequest, policy.request_id)
    assert request is not None
    customer = await session.get(Customer, request.customer_id)
    insurer = await session.get(Insurer, policy.insurer_id)
    assert customer is not None
    end = _utc(policy.effective_to).date().isoformat() if policy.effective_to else ""
    return customer, {
        "customer_name": customer_name(customer),
        "policy_number": policy.policy_number,
        "insurer": insurer.name if insurer else "",
        "end_date": end,
        "premium": money(policy.premium_amount),
    }


async def _live_policies(session: AsyncSession) -> list[InsurancePolicy]:
    result = await session.execute(select(InsurancePolicy).where(InsurancePolicy.status.in_(LIVE)))
    return list(result.scalars())


async def expire_policies(session: AsyncSession, now: datetime) -> int:
    if not (await get_rule(session, "expire_policies")).enabled:
        return 0
    count = 0
    for policy in await _live_policies(session):
        if policy.effective_to and _utc(policy.effective_to) < now:
            policy.status = InsurancePolicyStatus.EXPIRED
            audit(session, None, "policy_expired", "policy", policy.policy_id)
            count += 1
    return count


async def renewal_reminders(session: AsyncSession, today: date) -> int:
    rule = await get_rule(session, "renewal_reminder")
    if not rule.enabled:
        return 0
    thresholds = sorted(int(d) for d in rule.params.get("days", []))
    count = 0
    for policy in await _live_policies(session):
        if not policy.effective_to:
            continue
        days_left = (_utc(policy.effective_to).date() - today).days
        # Only the tightest threshold that applies, so a policy issued 20 days before
        # expiry gets the 30-day reminder once — not the 60-day one too.
        due = next((d for d in thresholds if 0 <= days_left <= d), None)
        if due is None:
            continue
        customer, context = await _policy_context(session, policy)
        if await enqueue(
            session,
            "renewal_reminder",
            customer,
            context,
            dedupe_key=f"renewal:{policy.policy_id}:{due}",
            policy_id=policy.policy_id,
        ):
            count += 1
    return count


async def auto_renewal_requests(session: AsyncSession, today: date) -> int:
    rule = await get_rule(session, "auto_renewal_request")
    if not rule.enabled:
        return 0
    lead = int(rule.params.get("days", 45))
    count = 0
    for policy in await _live_policies(session):
        if not policy.effective_to:
            continue
        days_left = (_utc(policy.effective_to).date() - today).days
        if not 0 <= days_left <= lead or await aftersales.open_renewal_for(session, policy.policy_id):
            continue
        try:
            renewal = await aftersales.create_renewal_request(session, policy, None)
        except aftersales.RenewalNotAllowed:
            continue
        audit(
            session,
            None,
            "renewal_started",
            "policy",
            policy.policy_id,
            request_id=renewal.request_id,
            automatic=True,
        )
        count += 1
    return count


async def installment_reminders(session: AsyncSession, today: date) -> dict[str, int]:
    due_rule = await get_rule(session, "installment_due")
    overdue_rule = await get_rule(session, "installment_overdue")
    days_before = int(due_rule.params.get("days_before", 3))
    days_after = sorted(int(d) for d in overdue_rule.params.get("days_after", []))
    result = await session.execute(
        select(PremiumInstallment, InsurancePolicy)
        .join(InsurancePolicy, InsurancePolicy.policy_id == PremiumInstallment.policy_id)
        .where(PremiumInstallment.status == InstallmentStatus.DUE, InsurancePolicy.status.in_(LIVE))
    )
    counts = {"due": 0, "overdue": 0}
    for item, policy in result.all():
        delta = (item.due_date - today).days
        kind, key = None, None
        if due_rule.enabled and 0 <= delta <= days_before:
            kind, key = "installment_due", f"inst_due:{item.installment_id}"
        elif overdue_rule.enabled and delta < 0:
            late = -delta
            step = max((d for d in days_after if late >= d), default=None)
            if step is not None:
                kind, key = "installment_overdue", f"inst_overdue:{item.installment_id}:{step}"
        if kind is None:
            continue
        customer, context = await _policy_context(session, policy)
        context |= {"amount": money(item.amount), "due_date": item.due_date.isoformat()}
        if await enqueue(session, kind, customer, context, dedupe_key=key, policy_id=policy.policy_id):
            counts["due" if kind == "installment_due" else "overdue"] += 1
    return counts


async def insurer_followups(session: AsyncSession, now: datetime) -> dict[str, int]:
    """Chase insurers that haven't quoted: email them again (if email is on) or flag it."""
    rule = await get_rule(session, "insurer_followup")
    counts = {"emailed": 0, "flagged": 0}
    if not rule.enabled:
        return counts
    wait = timedelta(hours=float(rule.params.get("hours", 48)))
    max_followups = int(rule.params.get("max_followups", 2))
    quoted = select(InsuranceQuote.submission_id)
    result = await session.execute(
        select(InsuranceSubmission).where(
            InsuranceSubmission.status.in_(
                [InsuranceSubmissionStatus.SENT, InsuranceSubmissionStatus.FOLLOW_UP_REQUIRED]
            ),
            InsuranceSubmission.sent_at.is_not(None),
            InsuranceSubmission.submission_id.not_in(quoted),
        )
    )
    for submission in result.scalars():
        last_contact = _utc(submission.last_follow_up_at or submission.sent_at)  # type: ignore[arg-type]
        if now - last_contact < wait:
            continue
        done = await session.scalar(
            select(func.count()).where(
                EmailAuditLog.submission_id == submission.submission_id, EmailAuditLog.action == "followup_sent"
            )
        )
        if (done or 0) >= max_followups:
            if submission.status != InsuranceSubmissionStatus.FOLLOW_UP_REQUIRED:
                submission.status = InsuranceSubmissionStatus.FOLLOW_UP_REQUIRED
                counts["flagged"] += 1
            continue
        if not settings.email_delivery_enabled:
            if submission.status != InsuranceSubmissionStatus.FOLLOW_UP_REQUIRED:
                submission.status = InsuranceSubmissionStatus.FOLLOW_UP_REQUIRED
                submission.last_follow_up_at = now
                counts["flagged"] += 1
            continue
        insurer = await session.get(Insurer, submission.insurer_id)
        if insurer is None:
            continue
        message = EmailMessage()
        message["From"] = settings.smtp_from_email
        message["To"] = insurer.email
        message["Subject"] = f"متابعة: {submission.subject}"
        message.set_content(
            f"السادة {insurer.name} المحترمين،\n\nنود متابعة طلب التسعير المرسل لكم بتاريخ "
            f"{_utc(submission.sent_at).date().isoformat()}، ونأمل تزويدنا بعرضكم في أقرب وقت.\n\n"  # type: ignore[arg-type]
            f"---- الرسالة الأصلية ----\n{submission.email_body}"
        )
        try:
            await mailer.send(message)
        except mailer.EmailDeliveryError as exc:
            submission.status = InsuranceSubmissionStatus.FOLLOW_UP_REQUIRED
            submission.delivery_error = str(exc)
            counts["flagged"] += 1
            continue
        submission.last_follow_up_at = now
        session.add(
            EmailAuditLog(
                submission_id=submission.submission_id,
                action="followup_sent",
                actor_name=SYSTEM,
                details=json.dumps({"to": insurer.email, "n": (done or 0) + 1}),
            )
        )
        counts["emailed"] += 1
    return counts


async def run_daily_jobs(session: AsyncSession, now: datetime | None = None) -> dict[str, Any]:
    """All scheduled jobs in one transaction; safe to run as often as you like."""
    now = now or datetime.now(UTC)
    today = now.date()
    summary: dict[str, Any] = {
        "expired": await expire_policies(session, now),
        "renewal_reminders": await renewal_reminders(session, today),
        "auto_renewals": await auto_renewal_requests(session, today),
        "installments": await installment_reminders(session, today),
        "insurer_followups": await insurer_followups(session, now),
    }
    await session.commit()
    return summary
