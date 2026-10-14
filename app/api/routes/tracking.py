"""Customers follow their own request: one-time code to their mobile, then a read-only status view."""

from __future__ import annotations

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import audit, client_ip
from app.api.deps import get_db_session
from app.automation import service
from app.automation.models import PortalAccess
from app.core.config import settings
from app.core.security import RateLimiter, hash_token, new_token
from app.insurance import aftersales, catalog
from app.insurance.models import (
    ClaimStatus,
    Customer,
    InstallmentStatus,
    InsuranceClaim,
    InsurancePolicy,
    InsuranceQuote,
    InsuranceRequest,
    InsuranceRequestStatus,
    InsuranceSubmission,
    InsuranceSubmissionStatus,
    Insurer,
    PremiumInstallment,
)
from app.insurance.schemas import normalize_saudi_mobile

router = APIRouter(prefix="/insurance/portal/track", tags=["portal"])

CODE_TTL = timedelta(minutes=10)
TOKEN_TTL = timedelta(minutes=30)
MAX_ATTEMPTS = 5
ip_limiter = RateLimiter(10, 600)
request_limiter = RateLimiter(3, 600)

STEPS = ["استلام الطلب", "مراجعة البيانات", "طلب العروض من الشركات", "اختيار العرض", "إصدار الوثيقة"]
STAGE = {
    InsuranceRequestStatus.NEW: (0, "استلمنا طلبك وسيبدأ فريقنا مراجعته."),
    InsuranceRequestStatus.NEEDS_INFO: (1, "نحتاج منك بعض البيانات الإضافية، وسيتواصل معك فريقنا."),
    InsuranceRequestStatus.UNDER_REVIEW: (1, "فريق الاكتتاب يراجع بيانات طلبك."),
    InsuranceRequestStatus.READY_FOR_QUOTE: (2, "أرسلنا طلبك لشركات التأمين وننتظر عروضها."),
    InsuranceRequestStatus.QUOTE_SELECTED: (3, "تم اختيار العرض، ونجهز إصدار الوثيقة."),
    InsuranceRequestStatus.POLICY_ISSUED: (4, "صدرت وثيقتك."),
    InsuranceRequestStatus.CLOSED: (-1, "أُغلق هذا الطلب. تواصل معنا إن كان لديك استفسار."),
}
CLAIM_LABELS = {
    ClaimStatus.REPORTED: "استلمنا البلاغ",
    ClaimStatus.SUBMITTED: "أُرسلت لشركة التأمين",
    ClaimStatus.UNDER_ASSESSMENT: "قيد التقييم",
    ClaimStatus.APPROVED: "اعتُمدت",
    ClaimStatus.REJECTED: "اعتذرت الشركة",
    ClaimStatus.PAID: "صُرف التعويض",
    ClaimStatus.CLOSED: "مغلقة",
}


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message})


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class StartTracking(BaseModel):
    request_number: str = Field(..., min_length=5, max_length=50)
    mobile: str

    @field_validator("mobile")
    @classmethod
    def _mobile(cls, value: str) -> str:
        return normalize_saudi_mobile(value)

    @field_validator("request_number")
    @classmethod
    def _number(cls, value: str) -> str:
        return value.strip().upper()


class VerifyTracking(StartTracking):
    code: str = Field(..., pattern=r"^\d{6}$")


async def _find_request(session: AsyncSession, number: str, mobile: str) -> InsuranceRequest | None:
    row = (
        await session.execute(
            select(InsuranceRequest, Customer)
            .join(Customer, Customer.customer_id == InsuranceRequest.customer_id)
            .where(InsuranceRequest.request_number == number)
        )
    ).first()
    if row is None or row[1].phone != mobile:
        return None
    return row[0]


@router.post("/start")
async def start(
    body: StartTracking, http: Request, session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    """Send a code if the number and mobile match. The answer is the same either way,
    so the endpoint can't be used to discover which requests or numbers exist."""
    ip = client_ip(http)
    if ip_limiter.blocked(ip) or request_limiter.blocked(body.request_number):
        raise _error(429, "TOO_MANY_REQUESTS", "Too many attempts; try again later")
    ip_limiter.hit(ip)
    request_limiter.hit(body.request_number)
    response: dict[str, Any] = {"sent": True}
    request = await _find_request(session, body.request_number, body.mobile)
    if request is None:
        return response
    code = f"{secrets.randbelow(10**6):06d}"
    access = PortalAccess(
        request_id=request.request_id, code_hash="", code_expires_at=datetime.now(UTC) + CODE_TTL
    )
    session.add(access)
    await session.flush()
    access.code_hash = hash_token(f"{access.access_id}:{code}")
    customer = await session.get(Customer, request.customer_id)
    assert customer is not None
    message = await service.enqueue(
        session,
        "otp",
        customer,
        {},
        body=f"رمز التحقق لمتابعة طلبك {request.request_number}: {code}\nصالح 10 دقائق. لا تشاركه مع أحد.",
        request_id=request.request_id,
    )
    if message is not None:
        await service.deliver_now(session, message)
    await session.commit()
    sandbox = await service.get_setting(session, "messaging_mode") != "live"
    if settings.environment == "development" and sandbox:
        response["dev_code"] = code  # messages aren't really sent in sandbox; lets developers test the flow
    return response


@router.post("/verify")
async def verify(
    body: VerifyTracking, http: Request, session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    invalid = _error(401, "INVALID_CODE", "The code is wrong or expired")
    request = await _find_request(session, body.request_number, body.mobile)
    if request is None:
        raise invalid
    access = (
        (
            await session.execute(
                select(PortalAccess)
                .where(PortalAccess.request_id == request.request_id, PortalAccess.verified_at.is_(None))
                .order_by(PortalAccess.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if access is None or _utc(access.code_expires_at) < datetime.now(UTC) or access.attempts >= MAX_ATTEMPTS:
        raise invalid
    access.attempts += 1
    if not hmac.compare_digest(access.code_hash, hash_token(f"{access.access_id}:{body.code}")):
        await session.commit()
        raise invalid
    token, access.token_hash = new_token()
    access.verified_at = datetime.now(UTC)
    access.token_expires_at = datetime.now(UTC) + TOKEN_TTL
    audit(session, None, "customer_tracking_opened", "request", request.request_id, ip=client_ip(http))
    await session.commit()
    return {"token": token, "expires_in": int(TOKEN_TTL.total_seconds())}


@router.get("")
async def status(
    x_track_token: str = Header(...),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    access = (
        await session.execute(select(PortalAccess).where(PortalAccess.token_hash == hash_token(x_track_token)))
    ).scalar_one_or_none()
    if access is None or access.token_expires_at is None or _utc(access.token_expires_at) < datetime.now(UTC):
        raise _error(401, "TRACKING_EXPIRED", "Session expired; request a new code")
    request = await session.get(InsuranceRequest, access.request_id)
    assert request is not None
    step, message = STAGE[request.status]

    contacted = await session.scalar(
        select(func.count()).where(
            InsuranceSubmission.request_id == request.request_id,
            InsuranceSubmission.status.in_(
                [
                    InsuranceSubmissionStatus.SENT,
                    InsuranceSubmissionStatus.FOLLOW_UP_REQUIRED,
                    InsuranceSubmissionStatus.REJECTED,
                ]
            ),
        )
    )
    quotes = await session.scalar(
        select(func.count())
        .select_from(InsuranceQuote)
        .join(InsuranceSubmission, InsuranceSubmission.submission_id == InsuranceQuote.submission_id)
        .where(InsuranceSubmission.request_id == request.request_id, InsuranceQuote.needs_review.is_(False))
    )
    view: dict[str, Any] = {
        "request_number": request.request_number,
        "line": catalog.line_label(request.insurance_type),
        "product": catalog.product_label(request.insurance_type, request.product_type),
        "created_at": _utc(request.created_at),
        "message": message,
        "closed": step < 0,
        "steps": [
            {"label": label, "done": 0 <= step and i < step, "current": i == step} for i, label in enumerate(STEPS)
        ],
        "insurers_contacted": contacted or 0,
        "quotes_received": quotes or 0,
        "policy": None,
        "claims": [],
    }
    policy = (
        await session.execute(select(InsurancePolicy).where(InsurancePolicy.request_id == request.request_id))
    ).scalar_one_or_none()
    if policy is not None:
        insurer = await session.get(Insurer, policy.insurer_id)
        installments = (
            (
                await session.execute(
                    select(PremiumInstallment)
                    .where(
                        PremiumInstallment.policy_id == policy.policy_id,
                        PremiumInstallment.status != InstallmentStatus.CANCELLED,
                    )
                    .order_by(PremiumInstallment.sequence)
                )
            )
            .scalars()
            .all()
        )
        view["policy"] = {
            "policy_number": policy.policy_number,
            "insurer": insurer.name if insurer else "",
            "status": policy.status.value,
            "effective_from": _utc(policy.effective_from) if policy.effective_from else None,
            "effective_to": _utc(policy.effective_to) if policy.effective_to else None,
            "premium": aftersales.to_money(policy.premium_amount),
            "installments": [
                {
                    "sequence": i.sequence,
                    "amount": i.amount,
                    "due_date": i.due_date,
                    "paid": i.status == InstallmentStatus.PAID,
                }
                for i in installments
            ],
        }
        claims = (
            (
                await session.execute(
                    select(InsuranceClaim)
                    .where(InsuranceClaim.policy_id == policy.policy_id)
                    .order_by(InsuranceClaim.created_at)
                )
            )
            .scalars()
            .all()
        )
        view["claims"] = [
            {"claim_number": c.claim_number, "status": CLAIM_LABELS[c.status], "incident_date": c.incident_date}
            for c in claims
        ]
    return view
