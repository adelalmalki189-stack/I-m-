"""Shared portfolio-risk data access — docs/OPERATIONAL_SPEC_V0.1.md §5.

Both GET /risk/portfolio-status (app/api/routes/risk.py) and the live
Scanner Loop (app/services/scanner_loop.py, which must gate a new ENTRY
signal against these same limits before sizing its RiskPlan) need the
exact same "how much risk is already deployed right now" computation —
extracted here once instead of duplicated, so the dashboard's own display
of portfolio risk can never drift from what actually gated a trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SignalStatus
from app.models.instruments import Instrument
from app.models.risk import PortfolioRiskConfig, RiskPlan
from app.models.signals import Signal
from app.models.trades import Trade


async def get_or_create_portfolio_config(session: AsyncSession) -> PortfolioRiskConfig:
    """V0.1 single-user singleton row (docs/OPERATIONAL_SPEC_V0.1.md §7) — get-or-create on first read."""
    result = await session.execute(select(PortfolioRiskConfig).limit(1))
    config = result.scalars().first()
    if config is None:
        config = PortfolioRiskConfig()
        session.add(config)
        await session.commit()
    return config


@dataclass(frozen=True, slots=True)
class PortfolioRiskInputs:
    open_signals_count: int
    open_signals_in_sector_count: int
    daily_realized_pnl: Decimal
    sum_open_risk: Decimal


async def get_portfolio_risk_inputs(session: AsyncSession, symbol: str | None = None) -> PortfolioRiskInputs:
    """`symbol`'s own sector decides the per-sector count (used when gating
    a specific new trade); a symbol with no sector on file, or no symbol at
    all (the general portfolio-status view, which isn't about any one
    trade), counts 0 for that check rather than guessing a group."""
    open_count = (
        await session.execute(select(func.count()).select_from(Signal).where(Signal.status == SignalStatus.ENTRY))
    ).scalar_one()

    instrument = await session.get(Instrument, symbol) if symbol is not None else None
    sector = instrument.sector if instrument is not None else None
    if sector is None:
        open_sector_count = 0
    else:
        open_sector_count = (
            await session.execute(
                select(func.count())
                .select_from(Signal)
                .join(Instrument, Instrument.symbol == Signal.symbol)
                .where(Signal.status == SignalStatus.ENTRY, Instrument.sector == sector)
            )
        ).scalar_one()

    sum_open_risk_raw = (
        await session.execute(
            select(func.coalesce(func.sum(RiskPlan.max_loss), 0))
            .join(Signal, Signal.signal_id == RiskPlan.signal_id)
            .where(Signal.status == SignalStatus.ENTRY)
        )
    ).scalar_one()

    today = datetime.now(UTC).date()
    daily_pnl_raw = (
        await session.execute(
            select(func.coalesce(func.sum(Trade.realized_pnl), 0)).where(func.date(Trade.user_exit_time) == today)
        )
    ).scalar_one()

    return PortfolioRiskInputs(
        open_signals_count=open_count,
        open_signals_in_sector_count=open_sector_count,
        daily_realized_pnl=Decimal(str(daily_pnl_raw)),
        sum_open_risk=Decimal(str(sum_open_risk_raw)),
    )
