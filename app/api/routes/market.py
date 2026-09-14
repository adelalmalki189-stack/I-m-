"""docs/API_SPEC_V0.1.md §1 — GET /market/context.

Reads whatever the live Market Context computation (a future scanner-loop
job — see app/engines/market_context_engine.py, which this endpoint does
NOT call directly) last published to Redis. This endpoint never computes a
regime itself: docs/DATA_SPEC_V0.1.md §5 makes Redis the single latest-value
source for market context, with no separate SQL table.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_redis_client
from app.core.market_context_cache import RedisLike, get_cached_market_context
from app.models.enums import MarketRegime, OverallMarketRegime, VixRegime

router = APIRouter(prefix="/market", tags=["market"])


class RegimeReading(BaseModel):
    price: Decimal
    regime: MarketRegime


class VixReading(BaseModel):
    value: Decimal
    regime: VixRegime


class MarketContextResponse(BaseModel):
    timestamp: str
    spy: RegimeReading
    qqq: RegimeReading
    vix: VixReading
    overall_market_regime: OverallMarketRegime


@router.get("/context", response_model=MarketContextResponse)
async def get_market_context(redis_client: RedisLike = Depends(get_redis_client)) -> MarketContextResponse:
    data = await get_cached_market_context(redis_client)
    if data is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "MARKET_CONTEXT_UNAVAILABLE",
                "message": "No market context has been published yet",
            },
        )
    return MarketContextResponse(**data)
