"""After-sale rules: commissions, premium installments, claim lifecycle, money parsing."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.insurance.models import (
    ClaimStatus,
    InsurancePolicy,
    InsurancePolicyStatus,
    InsuranceRequest,
    InsuranceRequestStatus,
    PremiumInstallment,
)

CENT = Decimal("0.01")
ALLOWED_INSTALLMENTS = (1, 2, 3, 4, 6, 12)


def to_money(value: object) -> Decimal | None:
    """Parse '1,250.5' / 1250.5 / Decimal into a 2-dp Decimal; None when empty or invalid."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").strip()).quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def commission_amount(premium: Decimal | None, rate: Decimal | None) -> Decimal | None:
    if premium is None or rate is None:
        return None
    return (premium * rate / 100).quantize(CENT, rounding=ROUND_HALF_UP)


def one_year_term_end(start: datetime) -> datetime:
    """Default policy term: one year, ending one second before the anniversary."""
    try:
        anniversary = start.replace(year=start.year + 1)
    except ValueError:  # Feb 29 → Mar 1 next year
        anniversary = start.replace(year=start.year + 1, month=3, day=1)
    return anniversary - timedelta(seconds=1)


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    # Clamp to the month's last day (e.g. Jan 31 + 1 month → Feb 28/29).
    for last in (31, 30, 29, 28):
        try:
            return date(year, month, min(day.day, last))
        except ValueError:
            continue
    raise ValueError("unreachable")


def build_installments(
    policy: InsurancePolicy, premium: Decimal, count: int, first_due: date
) -> list[PremiumInstallment]:
    """Split the premium into `count` equal payments spread over the year; rounding goes on the last one."""
    if count not in ALLOWED_INSTALLMENTS:
        raise ValueError(f"installments must be one of {ALLOWED_INSTALLMENTS}")
    base = (premium / count).quantize(CENT, rounding=ROUND_HALF_UP)
    step = 12 // count
    amounts = [base] * (count - 1) + [premium - base * (count - 1)]
    return [
        PremiumInstallment(
            policy_id=policy.policy_id,
            sequence=i + 1,
            amount=amount,
            due_date=_add_months(first_due, i * step),
        )
        for i, amount in enumerate(amounts)
    ]


C = ClaimStatus
CLAIM_TRANSITIONS: dict[ClaimStatus, set[ClaimStatus]] = {
    C.REPORTED: {C.SUBMITTED, C.CLOSED},
    C.SUBMITTED: {C.UNDER_ASSESSMENT, C.APPROVED, C.REJECTED},
    C.UNDER_ASSESSMENT: {C.APPROVED, C.REJECTED},
    C.APPROVED: {C.PAID, C.REJECTED},
    C.REJECTED: {C.SUBMITTED, C.CLOSED},  # resubmit after an objection, or close
    C.PAID: {C.CLOSED},
    C.CLOSED: set(),
}


def claim_next(status: ClaimStatus) -> list[str]:
    return sorted(s.value for s in CLAIM_TRANSITIONS[status])


class RenewalNotAllowed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def open_renewal_for(session: AsyncSession, policy_id: str) -> InsuranceRequest | None:
    result = await session.execute(
        select(InsuranceRequest).where(
            InsuranceRequest.renewal_of_policy_id == policy_id,
            InsuranceRequest.status != InsuranceRequestStatus.CLOSED,
        )
    )
    return result.scalars().first()


async def create_renewal_request(
    session: AsyncSession, policy: InsurancePolicy, sales_employee_id: str | None
) -> InsuranceRequest:
    """New request for the same customer and risk, linked to `policy` (flushed, not committed)."""
    if policy.status == InsurancePolicyStatus.CANCELLED:
        raise RenewalNotAllowed("POLICY_CANCELLED", "A cancelled policy cannot be renewed")
    if (existing := await open_renewal_for(session, policy.policy_id)) is not None:
        raise RenewalNotAllowed("RENEWAL_EXISTS", f"Renewal request {existing.request_number} already exists")
    original = await session.get(InsuranceRequest, policy.request_id)
    if original is None:
        raise RenewalNotAllowed("REQUEST_NOT_FOUND", "The policy's original request is missing")
    renewal = InsuranceRequest(
        customer_id=original.customer_id,
        request_number=f"IR-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}",
        insurance_type=original.insurance_type,
        product_type=original.product_type,
        request_source="renewal",
        status=InsuranceRequestStatus.NEW,
        coverage_amount=policy.coverage_amount or original.coverage_amount,
        customer_priority=original.customer_priority,
        request_details=original.request_details,
        preferred_insurers="[]",
        sales_employee_id=sales_employee_id or original.sales_employee_id,
        renewal_of_policy_id=policy.policy_id,
        notes=f"تجديد للوثيقة {policy.policy_number}",
    )
    session.add(renewal)
    await session.flush()
    return renewal
