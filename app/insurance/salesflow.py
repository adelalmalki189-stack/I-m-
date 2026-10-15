"""The sales cycle around a request: its document checklist, the authorization letter,
automatic assignment to a sales agent, and the hand-off to underwriting once complete.

Used by the API (on each event) and by the scheduled jobs (a sweep that catches anything
an event missed), so every step here is safe to repeat.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import audit
from app.automation import service as notifications
from app.automation.rules import get_rule
from app.core.config import settings
from app.insurance import authorization, catalog, documents, review, workflow
from app.insurance.models import (
    AuthorizationLetter,
    AuthorizationLetterStatus,
    Customer,
    InsuranceEmployeeRole,
    InsuranceRequest,
    InsuranceRequestStatus,
    RequestDocument,
    SalesEmployee,
    UnderwritingAssignment,
)

OPEN = (
    InsuranceRequestStatus.NEW,
    InsuranceRequestStatus.NEEDS_INFO,
    InsuranceRequestStatus.UNDER_REVIEW,
    InsuranceRequestStatus.READY_FOR_QUOTE,
    InsuranceRequestStatus.QUOTE_SELECTED,
)
# Requests still with sales: collecting documents and the signature.
WITH_SALES = (InsuranceRequestStatus.NEW, InsuranceRequestStatus.NEEDS_INFO)


def track_link(request: InsuranceRequest) -> str:
    return f"{settings.public_base_url}/dashboard/track.html?request={request.request_number}"


def display_name(customer: Customer) -> str:
    return customer.company_name or customer.full_name


def details(request: InsuranceRequest) -> dict[str, Any]:
    try:
        return dict(json.loads(request.request_details or "{}"))
    except json.JSONDecodeError:
        return {}


# --- Checklist ---------------------------------------------------------------------------


def document_view(doc: RequestDocument) -> dict[str, Any]:
    return {
        "document_id": doc.document_id,
        "document_type": doc.document_type,
        "document_label": catalog.document_label(doc.document_type),
        "file_name": doc.file_name,
        "mime_type": doc.mime_type,
        "size_bytes": doc.size_bytes,
        "review_status": doc.review_status,
        "findings": documents.findings_of(doc),
        "rejection_reason": doc.rejection_reason,
        "reviewed_at": doc.reviewed_at,
        "uploaded_by_staff": doc.uploaded_by_employee_id is not None,
        "created_at": doc.created_at,
    }


def letter_view(letter: AuthorizationLetter | None, full: bool = True) -> dict[str, Any] | None:
    if letter is None:
        return None
    signed = letter.status == AuthorizationLetterStatus.SIGNED
    view: dict[str, Any] = {
        "letter_id": letter.letter_id,
        "letter_number": letter.letter_number,
        "status": letter.status.value,
        "body_text": letter.body_text,
        "valid_until": letter.valid_until,
        "signed_at": letter.signed_at,
        "signer_name": letter.signer_name,
        "verification_code": letter.verification_code if signed else None,
        "document_id": letter.document_id,
    }
    if full and signed:
        view |= {
            "signer_id_last4": letter.signer_id_last4,
            "signed_mobile": authorization.mask_mobile(letter.signed_mobile),
            "signed_ip": letter.signed_ip,
            "content_hash": letter.content_hash,
            "verify_url": authorization.verify_url(letter),
        }
    return view


async def load_review(session: AsyncSession, request: InsuranceRequest) -> dict[str, Any]:
    customer = await session.get(Customer, request.customer_id)
    docs = (
        (
            await session.execute(
                select(RequestDocument)
                .where(RequestDocument.request_id == request.request_id, RequestDocument.claim_id.is_(None))
                .order_by(RequestDocument.created_at)
            )
        )
        .scalars()
        .all()
    )
    letter = await authorization.current(session, request.request_id)
    result = review.review_request(
        request.insurance_type,
        request.product_type,
        customer.customer_type if customer else "individual",
        details(request),
        request.coverage_amount,
        # The signed letter copy is shown through the letter, not as a file of its own.
        [document_view(d) for d in docs if d.document_type != catalog.AUTHORIZATION_LETTER],
        letter.status.value if letter else None,
    )
    result["letter"] = letter_view(letter)
    return result


def missing_uploads(result: dict[str, Any]) -> list[str]:
    """Required documents the customer still has to (re)upload; the letter is not one of them."""
    return [
        i["label"]
        for i in result["items"]
        if i["required"] and not i["generated"] and i["status"] in ("missing", review.FAILED, review.REJECTED)
    ]


async def issue_letter(
    session: AsyncSession,
    request: InsuranceRequest,
    customer: Customer,
    staff: SalesEmployee | None,
    notify: bool = True,
) -> AuthorizationLetter:
    """Issue a letter and (unless `notify` is off) send the customer the link to sign it."""
    letter = await authorization.issue(session, request, customer, staff.employee_id if staff else None)
    if not notify:
        return letter
    await notifications.enqueue(
        session,
        "authorization_letter",
        customer,
        {
            "customer_name": display_name(customer),
            "request_number": request.request_number,
            "track_link": track_link(request),
        },
        dedupe_key=f"authorization_letter:{letter.letter_id}",
        request_id=request.request_id,
        created_by=staff.full_name if staff else None,
    )
    return letter


# --- Assignment --------------------------------------------------------------------------


async def least_busy(session: AsyncSession, role: InsuranceEmployeeRole, column: Any) -> SalesEmployee | None:
    """The active employee in `role` with the fewest open requests in `column` (ties: by name)."""
    load = (
        select(column.label("employee_id"), func.count().label("n"))
        .where(InsuranceRequest.status.in_(OPEN), column.is_not(None))
        .group_by(column)
        .subquery()
    )
    row = (
        await session.execute(
            select(SalesEmployee)
            .outerjoin(load, load.c.employee_id == SalesEmployee.employee_id)
            .where(SalesEmployee.role == role.value, SalesEmployee.is_active.is_(True))
            .order_by(func.coalesce(load.c.n, 0), SalesEmployee.full_name, SalesEmployee.employee_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


async def assign_sales_agent(session: AsyncSession, request: InsuranceRequest) -> SalesEmployee | None:
    """Give an unassigned open request to the least busy sales agent."""
    if request.sales_employee_id or request.status not in OPEN:
        return None
    if not (await get_rule(session, "auto_assign_sales")).enabled:
        return None
    agent = await least_busy(session, InsuranceEmployeeRole.SALES_AGENT, InsuranceRequest.sales_employee_id)
    if agent is None:
        return None
    request.sales_employee_id = agent.employee_id
    audit(
        session, None, "request_auto_assigned", "request", request.request_id, sales_employee_id=agent.employee_id
    )
    await session.flush()
    return agent


async def maybe_hand_off(session: AsyncSession, request: InsuranceRequest) -> SalesEmployee | None:
    """Move a complete request to underwriting: every required document present with no
    open remark, and the authorization letter signed. Returns the underwriter, or None."""
    if request.status not in WITH_SALES or request.underwriter_id:
        return None
    if not (await get_rule(session, "auto_handoff_underwriting")).enabled:
        return None
    result = await load_review(session, request)
    if not result["ready"] or any(i["status"] == review.WARNING for i in result["items"]):
        return None  # a remark needs a person to accept the file first
    underwriter = await least_busy(session, InsuranceEmployeeRole.UNDERWRITER, InsuranceRequest.underwriter_id)
    if underwriter is None:
        return None
    workflow.transition(request, InsuranceRequestStatus.UNDER_REVIEW)
    request.underwriter_id = underwriter.employee_id
    request.underwriter_status = "under_review"
    session.add(
        UnderwritingAssignment(
            request_id=request.request_id,
            assigned_by_employee_id=request.sales_employee_id,
            assigned_to_employee_id=underwriter.employee_id,
            status="assigned",
            notes="تحويل آلي: المستندات مكتملة وخطاب التفويض موقّع",
        )
    )
    audit(
        session,
        None,
        "request_handed_to_underwriting",
        "request",
        request.request_id,
        underwriter_id=underwriter.employee_id,
        automatic=True,
    )
    await session.flush()
    return underwriter
