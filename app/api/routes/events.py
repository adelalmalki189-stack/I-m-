"""docs/API_SPEC_V0.1.md §6 — GET /events/upcoming.

Surfaces the market_events calendar (docs/DATA_SPEC_V0.1.md §8) so the
dashboard can show a "⚠️ Event Risk" warning proactively — before any
Signal even exists for that symbol, per Methodology §21.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.models.enums import EventSeverity, EventType
from app.models.market_events import MarketEvent

router = APIRouter(prefix="/events", tags=["events"])

DEFAULT_UPCOMING_LIMIT = 50


class UpcomingEvent(BaseModel):
    event_id: str
    symbol: str | None
    event_type: EventType
    event_time: datetime
    severity: EventSeverity
    source: str


@router.get("/upcoming", response_model=list[UpcomingEvent])
async def get_upcoming_events(
    symbol: str | None = None,
    limit: int = DEFAULT_UPCOMING_LIMIT,
    session: AsyncSession = Depends(get_db_session),
) -> list[UpcomingEvent]:
    query = select(MarketEvent).where(MarketEvent.event_time >= datetime.now(UTC))
    if symbol:
        query = query.where(MarketEvent.symbol == symbol)
    query = query.order_by(MarketEvent.event_time).limit(limit)

    result = await session.execute(query)
    return [
        UpcomingEvent(
            event_id=e.event_id, symbol=e.symbol, event_type=e.event_type,
            event_time=e.event_time, severity=e.severity, source=e.source,
        )
        for e in result.scalars().all()
    ]
