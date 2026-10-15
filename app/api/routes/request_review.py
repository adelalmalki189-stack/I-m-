"""Required documents, automatic review, staff decisions and the authorization letter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import audit, client_ip, get_current_staff
from app.api.deps import get_db_session
from app.automation import service as notifications
from app.core.config import settings
from app.core.security import RateLimiter
from app.insurance import authorization, catalog, documents, review, workflow
from app.insurance.models import (
    AuthorizationLetter,
    AuthorizationLetterStatus,
    Customer,
    InsuranceRequest,
    InsuranceRequestStatus,
    RequestDocument,
    SalesEmployee,
)

router = APIRouter(prefix="/insurance", tags=["documents"], dependencies=[Depends(get_current_staff)])
public_router = APIRouter(prefix="/insurance/portal", tags=["portal"])
verify_limiter = RateLimiter(30, 600)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message})


def track_link(request: InsuranceRequest) -> str:
    return f"{settings.public_base_url}/dashboard/track.html?request={request.request_number}"


def _details(request: InsuranceRequest) -> dict[str, Any]:
    try:
        return dict(json.loads(request.request_details or "{}"))
    except json.JSONDecodeError:
        return {}


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
        _details(request),
        request.coverage_amount,
        # The signed letter copy is shown through the letter, not as a file of its own.
        [document_view(d) for d in docs if d.document_type != catalog.AUTHORIZATION_LETTER],
        letter.status.value if letter else None,
    )
    result["letter"] = letter_view(letter)
    return result


async def _request(session: AsyncSession, request_id: str) -> InsuranceRequest:
    request = await session.get(InsuranceRequest, request_id)
    if request is None:
        raise _error(404, "REQUEST_NOT_FOUND", "Request not found")
    return request


async def _customer(session: AsyncSession, request: InsuranceRequest) -> Customer:
    customer = await session.get(Customer, request.customer_id)
    assert customer is not None
    return customer


def _display(customer: Customer) -> str:
    return customer.company_name or customer.full_name


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
            "customer_name": _display(customer),
            "request_number": request.request_number,
            "track_link": track_link(request),
        },
        dedupe_key=f"authorization_letter:{letter.letter_id}",
        request_id=request.request_id,
        created_by=staff.full_name if staff else None,
    )
    return letter


# --- Staff ---------------------------------------------------------------------------------


@router.get("/requests/{request_id}/review")
async def get_review(request_id: str, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    return await load_review(session, await _request(session, request_id))


class DocumentDecision(BaseModel):
    decision: Literal["accept", "reject"]
    reason: str | None = Field(default=None, max_length=500)


@router.post("/documents/{document_id}/decision")
async def decide_document(
    document_id: str,
    body: DocumentDecision,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> dict[str, Any]:
    doc = await session.get(RequestDocument, document_id)
    if doc is None:
        raise _error(404, "DOCUMENT_NOT_FOUND", "Document not found")
    if doc.document_type == catalog.AUTHORIZATION_LETTER:
        raise _error(409, "LETTER_IS_SIGNED", "The signed letter is verified by its signature")
    if body.decision == "reject" and not (body.reason or "").strip():
        raise _error(422, "REASON_REQUIRED", "Give the customer a reason for rejecting the document")
    doc.review_status = review.ACCEPTED if body.decision == "accept" else review.REJECTED
    doc.is_verified = body.decision == "accept"
    doc.rejection_reason = body.reason.strip() if body.decision == "reject" and body.reason else None
    doc.reviewed_by_id = staff.employee_id
    doc.reviewed_at = datetime.now(UTC)
    audit(session, staff, f"document_{body.decision}ed", "request", doc.request_id, document_id=document_id)
    await session.commit()
    return await load_review(session, await _request(session, doc.request_id))


class DocumentClassify(BaseModel):
    document_type: str = Field(..., min_length=2, max_length=80)


@router.patch("/documents/{document_id}")
async def classify_document(
    document_id: str,
    body: DocumentClassify,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> dict[str, Any]:
    """Set the type of an unclassified (or wrongly classified) upload, and review it again."""
    doc = await session.get(RequestDocument, document_id)
    if doc is None:
        raise _error(404, "DOCUMENT_NOT_FOUND", "Document not found")
    request = await _request(session, doc.request_id)
    customer = await _customer(session, request)
    spec = documents.spec_for(
        request.insurance_type, request.product_type, customer.customer_type, body.document_type
    )
    if spec is None or spec["generated"]:
        raise _error(422, "UNKNOWN_DOCUMENT_TYPE", "This document type does not belong to the request")
    path = documents.resolve_path(doc.storage_key)
    content = path.read_bytes() if path else b""
    doc.document_type = body.document_type
    doc.review_status, findings = review.review_file(content, doc.mime_type, body.document_type, spec)
    doc.review_findings = json.dumps(findings, ensure_ascii=False)
    doc.is_verified, doc.rejection_reason, doc.reviewed_at = False, None, None
    audit(session, staff, "document_classified", "request", doc.request_id, document_type=body.document_type)
    await session.commit()
    return await load_review(session, request)


@router.post("/requests/{request_id}/request-documents")
async def request_missing_documents(
    request_id: str,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> dict[str, Any]:
    """Message the customer the list of missing / rejected documents and mark the request needs_info."""
    request = await _request(session, request_id)
    result = await load_review(session, request)
    needed = [
        i["label"]
        for i in result["items"]
        if i["required"] and not i["generated"] and i["status"] in ("missing", review.FAILED, review.REJECTED)
    ]
    if not needed:
        raise _error(409, "NOTHING_MISSING", "No document is missing or rejected")
    customer = await _customer(session, request)
    await notifications.enqueue(
        session,
        "documents_missing",
        customer,
        {
            "customer_name": _display(customer),
            "request_number": request.request_number,
            "missing": "، ".join(needed),
            "track_link": track_link(request),
        },
        request_id=request.request_id,
        created_by=staff.full_name,
    )
    if InsuranceRequestStatus.NEEDS_INFO.value in workflow.allowed_next(request.status):
        workflow.transition(request, InsuranceRequestStatus.NEEDS_INFO)
    audit(session, staff, "documents_requested", "request", request_id, missing=needed)
    await session.commit()
    return await load_review(session, request)


@router.post("/requests/{request_id}/authorization-letter", status_code=201)
async def create_authorization_letter(
    request_id: str,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> dict[str, Any]:
    """Issue (or re-issue) the letter; a previous letter, signed or not, is revoked."""
    request = await _request(session, request_id)
    letter = await issue_letter(session, request, await _customer(session, request), staff)
    audit(session, staff, "authorization_letter_issued", "request", request_id, letter_number=letter.letter_number)
    await session.commit()
    return await load_review(session, request)


# --- Public verification --------------------------------------------------------------------


@public_router.get("/verify/{code}")
async def verify_letter(
    code: str, http: Request, session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    """Anyone holding a signed letter can check it is genuine and unchanged."""
    ip = client_ip(http)
    if verify_limiter.blocked(ip):
        raise _error(429, "TOO_MANY_REQUESTS", "Too many attempts; try again later")
    verify_limiter.hit(ip)
    letter = (
        await session.execute(
            select(AuthorizationLetter).where(AuthorizationLetter.verification_code == code.strip().upper())
        )
    ).scalar_one_or_none()
    if letter is None or letter.signed_at is None:
        raise _error(404, "LETTER_NOT_FOUND", "No signed letter has this code")
    request = await session.get(InsuranceRequest, letter.request_id)
    expired = letter.valid_until < datetime.now(UTC).date()
    return {
        "letter_number": letter.letter_number,
        "request_number": request.request_number if request else None,
        "status": "revoked"
        if letter.status == AuthorizationLetterStatus.REVOKED
        else ("expired" if expired else "valid"),
        "intact": letter.content_hash == authorization.signature_hash(letter),
        "signer_name": letter.signer_name,
        "signed_at": letter.signed_at,
        "valid_until": letter.valid_until,
        "body_text": letter.body_text,
        "content_hash": letter.content_hash,
        "broker": settings.broker_legal_name,
    }
