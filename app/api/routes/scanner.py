"""docs/API_SPEC_V0.1.md §2 — Scanner / Watchlist.

GET /scanner/candidates surfaces which symbols passed Initial Filters
(Methodology §2) — this transparency is deliberate, so the dashboard can
show "what the system is watching now" even before any Signal exists for
a symbol. Reads `instruments.is_active` (docs/DATA_SPEC_V0.1.md §1)
directly; there is no separate scanner_config table in V0.1 — filter
thresholds live in strategy_parameters like every other tunable constant.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.models.instruments import Instrument

router = APIRouter(prefix="/scanner", tags=["scanner"])

DEFAULT_CANDIDATES_LIMIT = 50


class ScannerCandidate(BaseModel):
    symbol: str
    exchange: str
    sector: str | None
    industry: str | None


@router.get("/candidates", response_model=list[ScannerCandidate])
async def get_scanner_candidates(
    limit: int = DEFAULT_CANDIDATES_LIMIT,
    session: AsyncSession = Depends(get_db_session),
) -> list[ScannerCandidate]:
    result = await session.execute(
        select(Instrument).where(Instrument.is_active.is_(True)).order_by(Instrument.symbol).limit(limit)
    )
    return [
        ScannerCandidate(symbol=i.symbol, exchange=i.exchange, sector=i.sector, industry=i.industry)
        for i in result.scalars().all()
    ]
