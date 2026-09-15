"""docs/API_SPEC_V0.1.md §4 — Risk.

`portfolio_risk_config` is a V0.1 single-user singleton row (get-or-create
on first read) per docs/OPERATIONAL_SPEC_V0.1.md §7's explicit single-user
decision — there is no per-user scoping to worry about yet.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.models.risk import PortfolioRiskConfig
from app.services.portfolio_risk import get_or_create_portfolio_config, get_portfolio_risk_inputs

router = APIRouter(prefix="/risk", tags=["risk"])


class RiskConfigResponse(BaseModel):
    account_size: Decimal
    risk_percent: Decimal
    max_concurrent_signals: int
    max_concurrent_per_sector: int
    daily_max_loss_pct: Decimal
    max_total_deployed_risk_pct: Decimal
    enforce_pdt_limit: bool


class RiskConfigUpdateRequest(BaseModel):
    account_size: Decimal | None = None
    risk_percent: Decimal | None = None


def _to_config_response(config: PortfolioRiskConfig) -> RiskConfigResponse:
    return RiskConfigResponse(
        account_size=config.account_size,
        risk_percent=config.risk_percent,
        max_concurrent_signals=config.max_concurrent_signals,
        max_concurrent_per_sector=config.max_concurrent_per_sector,
        daily_max_loss_pct=config.daily_max_loss_pct,
        max_total_deployed_risk_pct=config.max_total_deployed_risk_pct,
        enforce_pdt_limit=config.enforce_pdt_limit,
    )


@router.get("/config", response_model=RiskConfigResponse)
async def get_risk_config(session: AsyncSession = Depends(get_db_session)) -> RiskConfigResponse:
    return _to_config_response(await get_or_create_portfolio_config(session))


@router.put("/config", response_model=RiskConfigResponse)
async def update_risk_config(
    body: RiskConfigUpdateRequest, session: AsyncSession = Depends(get_db_session)
) -> RiskConfigResponse:
    config = await get_or_create_portfolio_config(session)
    if body.account_size is not None:
        config.account_size = body.account_size
    if body.risk_percent is not None:
        config.risk_percent = body.risk_percent
    await session.commit()
    return _to_config_response(config)


class PortfolioStatusResponse(BaseModel):
    open_signals_count: int
    max_concurrent_signals: int
    total_deployed_risk_pct: Decimal
    daily_realized_pnl: Decimal
    daily_max_loss_hit: bool


@router.get("/portfolio-status", response_model=PortfolioStatusResponse)
async def get_portfolio_status(session: AsyncSession = Depends(get_db_session)) -> PortfolioStatusResponse:
    config = await get_or_create_portfolio_config(session)
    inputs = await get_portfolio_risk_inputs(session)

    max_daily_loss = config.account_size * config.daily_max_loss_pct / Decimal(100)
    total_deployed_risk_pct = (
        (inputs.sum_open_risk / config.account_size) * Decimal(100) if config.account_size else Decimal(0)
    )

    return PortfolioStatusResponse(
        open_signals_count=inputs.open_signals_count,
        max_concurrent_signals=config.max_concurrent_signals,
        total_deployed_risk_pct=total_deployed_risk_pct,
        daily_realized_pnl=inputs.daily_realized_pnl,
        daily_max_loss_hit=inputs.daily_realized_pnl <= -max_daily_loss,
    )
