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
from app.insurance import authorization, catalog, documents, review, salesflow, workflow
from app.insurance.models import (
    AuthorizationLetter,
    AuthorizationLetterStatus,
    Customer,
    InsuranceRequest,
    InsuranceRequestStatus,
    RequestDocument,
    SalesEmployee,
)
from app.insurance.salesflow import load_review

router = APIRouter(prefix="/insurance", tags=["documents"], dependencies=[Depends(get_current_staff)])
public_router = APIRouter(prefix="/insurance/portal", tags=["portal"])
verify_limiter = RateLimiter(30, 600)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message})


async def _request(session: AsyncSession, request_id: str) -> InsuranceRequest:
    request = await session.get(InsuranceRequest, request_id)
    if request is None:
        raise _error(404, "REQUEST_NOT_FOUND", "Request not found")
    return request


async def _customer(session: AsyncSession, request: InsuranceRequest) -> Customer:
    customer = await session.get(Customer, request.customer_id)
    assert customer is not None
    return customer


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
    request = await _request(session, doc.request_id)
    await salesflow.maybe_hand_off(session, request)
    await session.commit()
    return await load_review(session, request)


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
    await salesflow.maybe_hand_off(session, request)
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
    needed = salesflow.missing_uploads(result)
    if not needed:
        raise _error(409, "NOTHING_MISSING", "No document is missing or rejected")
    customer = await _customer(session, request)
    await notifications.enqueue(
        session,
        "documents_missing",
        customer,
        {
            "customer_name": salesflow.display_name(customer),
            "request_number": request.request_number,
            "missing": "، ".join(needed),
            "track_link": salesflow.track_link(request),
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
    letter = await salesflow.issue_letter(session, request, await _customer(session, request), staff)
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
        "signer_title": letter.signer_title,
        "signed_at": letter.signed_at,
        "valid_until": letter.valid_until,
        "body_text": letter.body_text,
        "content_hash": letter.content_hash,
        "broker": settings.broker_legal_name,
    }
