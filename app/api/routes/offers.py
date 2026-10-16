"""Staff send the customer a selection of offers to choose from, and see the outcome."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import audit, get_current_staff
from app.api.deps import get_db_session
from app.automation import service as notifications
from app.insurance import offers, salesflow
from app.insurance.models import Customer, InsuranceRequest, OfferPresentationStatus, SalesEmployee

router = APIRouter(prefix="/insurance", tags=["offers"], dependencies=[Depends(get_current_staff)])


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message})


async def _request(session: AsyncSession, request_id: str) -> InsuranceRequest:
    request = await session.get(InsuranceRequest, request_id)
    if request is None:
        raise _error(404, "REQUEST_NOT_FOUND", "Request not found")
    return request


class PublishOffers(BaseModel):
    quote_ids: list[str] = Field(..., min_length=1, max_length=10)
    recommended_quote_id: str | None = None
    note: str | None = Field(default=None, max_length=1000)


@router.get("/requests/{request_id}/offers")
async def get_offers(request_id: str, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any] | None:
    await _request(session, request_id)
    return await offers.view(session, await offers.current(session, request_id), staff=True)


@router.post("/requests/{request_id}/offers", status_code=201)
async def publish_offers(
    request_id: str,
    body: PublishOffers,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> dict[str, Any] | None:
    """Show these offers to the customer (replacing any earlier selection) and message them."""
    request = await _request(session, request_id)
    try:
        p = await offers.publish(
            session, request, body.quote_ids, body.recommended_quote_id, body.note, staff.employee_id
        )
    except offers.OfferError as exc:
        raise _error(exc.status, exc.code, str(exc)) from exc
    customer = await session.get(Customer, request.customer_id)
    assert customer is not None
    await notifications.enqueue(
        session,
        "offers_ready",
        customer,
        {
            "customer_name": salesflow.display_name(customer),
            "request_number": request.request_number,
            "count": len(body.quote_ids),
            "track_link": salesflow.track_link(request),
        },
        dedupe_key=f"offers_ready:{p.presentation_id}",
        request_id=request_id,
        created_by=staff.full_name,
    )
    audit(session, staff, "offers_published", "request", request_id, quotes=body.quote_ids)
    await session.commit()
    return await offers.view(session, p, staff=True)


@router.post("/requests/{request_id}/offers/withdraw")
async def withdraw_offers(
    request_id: str,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> dict[str, Any] | None:
    p = await offers.current(session, request_id)
    if p is None or p.status != OfferPresentationStatus.PUBLISHED:
        raise _error(409, "NO_OFFERS_TO_ACCEPT", "No offers are awaiting the customer's choice")
    p.status = OfferPresentationStatus.WITHDRAWN
    audit(session, staff, "offers_withdrawn", "request", request_id)
    await session.commit()
    return None
