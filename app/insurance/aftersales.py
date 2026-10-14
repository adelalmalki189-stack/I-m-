"""After-sale rules: commissions, premium installments, claim lifecycle, money parsing."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.insurance.models import (
    ClaimStatus,
    InsurancePolicy,
    Insurer,
    InsurerCommissionRate,
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


async def commission_rate_for(session: AsyncSession, insurer_id: str, line: str) -> Decimal | None:
    """The insurer's rate for this line, else its default rate, else None."""
    result = await session.execute(
        select(InsurerCommissionRate.rate).where(
            InsurerCommissionRate.insurer_id == insurer_id, InsurerCommissionRate.line == line
        )
    )
    rate = result.scalar_one_or_none()
    if rate is not None:
        return Decimal(rate)
    insurer = await session.get(Insurer, insurer_id)
    return (
        Decimal(insurer.default_commission_rate)
        if insurer and insurer.default_commission_rate is not None
        else None
    )


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
