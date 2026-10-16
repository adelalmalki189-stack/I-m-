"""Staff issue payment requests, check transfer proofs and confirm payments."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import audit, get_current_staff
from app.api.deps import get_db_session
from app.automation import service as notifications
from app.insurance import payments, salesflow
from app.insurance.models import Customer, InsuranceRequest, PaymentRequest, PaymentRequestStatus, SalesEmployee

router = APIRouter(prefix="/insurance", tags=["payments"], dependencies=[Depends(get_current_staff)])


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message})


async def _context(session: AsyncSession, request: InsuranceRequest) -> tuple[Customer, dict[str, str]]:
    customer = await session.get(Customer, request.customer_id)
    assert customer is not None
    return customer, {
        "customer_name": salesflow.display_name(customer),
        "request_number": request.request_number,
        "track_link": salesflow.track_link(request),
    }


async def send_payment_request(
    session: AsyncSession, request: InsuranceRequest, p: PaymentRequest, created_by: str | None
) -> None:
    customer, context = await _context(session, request)
    await notifications.enqueue(
        session,
        "payment_request",
        customer,
        context | {"amount": payments.money(p.amount_due), "due_date": p.due_date.isoformat()},
        dedupe_key=f"payment_request:{p.payment_id}",
        request_id=request.request_id,
        created_by=created_by,
    )


class IssuePayment(BaseModel):
    installments: int = 1
    vat_amount: Decimal | None = Field(default=None, ge=0)
    due_days: int | None = Field(default=None, ge=0, le=90)


@router.post("/requests/{request_id}/payment-request", status_code=201)
async def issue_payment_request(
    request_id: str,
    body: IssuePayment,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> dict[str, Any] | None:
    request = await session.get(InsuranceRequest, request_id)
    if request is None:
        raise _error(404, "REQUEST_NOT_FOUND", "Request not found")
    customer = await session.get(Customer, request.customer_id)
    assert customer is not None
    try:
        p = await payments.issue(
            session, request, customer, body.installments, staff.employee_id, body.vat_amount, body.due_days
        )
    except payments.PaymentError as exc:
        raise _error(exc.status, exc.code, str(exc)) from exc
    await send_payment_request(session, request, p, staff.full_name)
    audit(session, staff, "payment_requested", "request", request_id, amount=str(p.amount_due))
    await session.commit()
    return payments.view(p, staff=True)


@router.get("/requests/{request_id}/payment-request")
async def get_payment_request(
    request_id: str, session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any] | None:
    return payments.view(await payments.latest(session, request_id), staff=True)


@router.get("/payment-requests")
async def list_payment_requests(
    status: Literal["issued", "proof_submitted", "paid"] | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Open payment requests (newest first), e.g. proofs awaiting a check."""
    stmt = (
        select(PaymentRequest, InsuranceRequest, Customer)
        .join(InsuranceRequest, InsuranceRequest.request_id == PaymentRequest.request_id)
        .join(Customer, Customer.customer_id == InsuranceRequest.customer_id)
    )
    stmt = stmt.where(
        PaymentRequest.status == PaymentRequestStatus(status)
        if status
        else PaymentRequest.status.in_(payments.OPEN)
    )
    rows = await session.execute(stmt.order_by(PaymentRequest.created_at.desc()).limit(200))
    return [
        (payments.view(p, staff=True) or {})
        | {
            "request_id": r.request_id,
            "request_number": r.request_number,
            "customer_name": c.company_name or c.full_name,
        }
        for p, r, c in rows.all()
    ]


async def _payment(session: AsyncSession, payment_id: str) -> tuple[PaymentRequest, InsuranceRequest]:
    p = await session.get(PaymentRequest, payment_id)
    request = await session.get(InsuranceRequest, p.request_id) if p else None
    if p is None or request is None:
        raise _error(404, "PAYMENT_NOT_FOUND", "Payment request not found")
    return p, request


class ConfirmPayment(BaseModel):
    method: Literal["bank_transfer", "online", "cheque", "cash"] = "bank_transfer"
    bank_reference: str | None = Field(default=None, max_length=100)
    paid_on: date | None = None


@router.post("/payment-requests/{payment_id}/confirm")
async def confirm_payment(
    payment_id: str,
    body: ConfirmPayment,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> dict[str, Any] | None:
    p, request = await _payment(session, payment_id)
    try:
        await payments.confirm(session, p, staff.employee_id, body.method, body.bank_reference, body.paid_on)
    except payments.PaymentError as exc:
        raise _error(exc.status, exc.code, str(exc)) from exc
    customer, context = await _context(session, request)
    await notifications.enqueue(
        session,
        "payment_received",
        customer,
        context | {"amount": payments.money(p.amount_due)},
        dedupe_key=f"payment_received:{p.payment_id}",
        request_id=request.request_id,
        created_by=staff.full_name,
    )
    audit(
        session,
        staff,
        "payment_confirmed",
        "request",
        request.request_id,
        amount=str(p.amount_due),
        method=body.method,
    )
    await session.commit()
    return payments.view(p, staff=True)


class RejectProof(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


@router.post("/payment-requests/{payment_id}/reject-proof")
async def reject_payment_proof(
    payment_id: str,
    body: RejectProof,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> dict[str, Any] | None:
    p, request = await _payment(session, payment_id)
    try:
        payments.reject_proof(p, body.reason)
    except payments.PaymentError as exc:
        raise _error(exc.status, exc.code, str(exc)) from exc
    customer, context = await _context(session, request)
    await notifications.enqueue(
        session,
        "payment_proof_rejected",
        customer,
        context | {"reason": body.reason.strip()},
        request_id=request.request_id,
        created_by=staff.full_name,
    )
    audit(session, staff, "payment_proof_rejected", "request", request.request_id)
    await session.commit()
    return payments.view(p, staff=True)
