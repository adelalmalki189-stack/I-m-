"""Session-aware candle history queries backing the live Scanner Loop
(app/services/scanner_loop.py). Every query here translates "which
sessions matter" — via `trading_calendar`, per
docs/OPERATIONAL_SPEC_V0.1.md §1 — into the plain Candle DTOs
app/engines/feature_engine/pipeline.py already expects, so the loop never
guesses session boundaries or invents its own calendar logic.

Baseline/continuity lookups batch their candle fetch into a single query
covering the whole date range rather than one query per day — this runs
once per live candle in production, so an O(days) round-trip count would
scale badly over a long-running process.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data_providers.schemas import Candle as CandleDTO
from app.models.candles import Candle5m
from app.models.enums import DataStatus, SessionType
from app.models.trading_calendar import TradingCalendarDay

DEFAULT_MIN_BASELINE_DAYS = 20
DEFAULT_CONTINUITY_LOOKBACK_SESSIONS = 30


def to_candle_dto(row: Candle5m) -> CandleDTO:
    return CandleDTO(
        symbol=row.symbol, timestamp=row.timestamp, open=row.open, high=row.high, low=row.low,
        close=row.close, volume=row.volume, vwap=row.vwap, is_complete=row.is_complete,
        source="db", data_status=DataStatus.VALID,
    )


async def _candles_between(
    session: AsyncSession, symbol: str, start: datetime, end: datetime
) -> list[Candle5m]:
    result = await session.execute(
        select(Candle5m)
        .where(
            Candle5m.symbol == symbol,
            Candle5m.timestamp >= start,
            Candle5m.timestamp <= end,
            Candle5m.is_complete.is_(True),
        )
        .order_by(Candle5m.timestamp)
    )
    return list(result.scalars().all())


async def get_session_candles(session: AsyncSession, symbol: str, day: date) -> list[Candle5m]:
    """A date with no `trading_calendar` row is CLOSED — per
    docs/OPERATIONAL_SPEC_V0.1.md §1, only FULL/HALF days are ever written
    there, so a missing row already means closed, never an unknown state."""
    calendar_row = await session.get(TradingCalendarDay, day)
    if calendar_row is None or calendar_row.market_open is None or calendar_row.market_close is None:
        return []
    return await _candles_between(session, symbol, calendar_row.market_open, calendar_row.market_close)


async def _prior_session_windows(
    session: AsyncSession, before: date, limit: int, *, full_only: bool
) -> list[TradingCalendarDay]:
    query = select(TradingCalendarDay).where(
        TradingCalendarDay.date < before,
        TradingCalendarDay.market_open.is_not(None),
        TradingCalendarDay.market_close.is_not(None),
    )
    if full_only:
        query = query.where(TradingCalendarDay.session_type == SessionType.FULL)
    query = query.order_by(TradingCalendarDay.date.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


def _require_bounds(window: TradingCalendarDay) -> tuple[datetime, datetime]:
    """A FULL/HALF calendar row always carries both bounds together
    (populate_trading_calendar.py sets them as a pair), and
    `_prior_session_windows` already filters out any row missing either —
    this just narrows the type for the arithmetic below."""
    assert window.market_open is not None
    assert window.market_close is not None
    return window.market_open, window.market_close


async def _batch_session_candles(
    session: AsyncSession, symbol: str, windows: list[TradingCalendarDay]
) -> dict[date, list[Candle5m]]:
    """One query covering every window's span instead of one query per
    window — each candle is then bucketed back to its own session by date,
    since sessions never span midnight."""
    by_date: dict[date, list[Candle5m]] = {w.date: [] for w in windows}
    if not windows:
        return by_date

    bounds = {w.date: _require_bounds(w) for w in windows}
    overall_start = min(start for start, _ in bounds.values())
    overall_end = max(end for _, end in bounds.values())
    all_candles = await _candles_between(session, symbol, overall_start, overall_end)

    for candle in all_candles:
        candle_day = candle.timestamp.date()
        window_bounds = bounds.get(candle_day)
        if window_bounds is not None and window_bounds[0] <= candle.timestamp <= window_bounds[1]:
            by_date[candle_day].append(candle)
    return by_date


async def get_previous_session_candles(session: AsyncSession, symbol: str, day: date) -> list[Candle5m] | None:
    """The most recent trading day (FULL or HALF) strictly before `day` —
    used for PDH/PDL, which cares about the actual prior session's prices
    regardless of RVOL-baseline eligibility."""
    windows = await _prior_session_windows(session, day, 1, full_only=False)
    if not windows:
        return None
    return await get_session_candles(session, symbol, windows[0].date)


async def get_baseline_sessions(
    session: AsyncSession, symbol: str, day: date, *, min_valid_days: int = DEFAULT_MIN_BASELINE_DAYS
) -> list[list[Candle5m]]:
    """Excludes HALF days per docs/CALCULATION_SPEC_V0.1.md §3.2 — they
    would distort the RVOL baseline average."""
    windows = await _prior_session_windows(session, day, min_valid_days, full_only=True)
    by_date = await _batch_session_candles(session, symbol, windows)
    return [by_date[w.date] for w in windows]


async def get_continuity_candles(
    session: AsyncSession,
    symbol: str,
    day: date,
    *,
    lookback_sessions: int = DEFAULT_CONTINUITY_LOOKBACK_SESSIONS,
) -> list[Candle5m]:
    """A bounded window (not the symbol's entire history) feeding EMA/ATR's
    `prior_candles_for_continuity` parameter
    (app/engines/feature_engine/pipeline.py) — exponential smoothing makes
    very old bars negligible anyway, and bounding this keeps per-candle
    cost from growing unbounded over a long-running process."""
    windows = await _prior_session_windows(session, day, lookback_sessions, full_only=False)
    windows.sort(key=lambda w: w.date)  # oldest first, matching chronological candle order
    by_date = await _batch_session_candles(session, symbol, windows)

    candles: list[Candle5m] = []
    for w in windows:
        candles.extend(by_date[w.date])
    return candles
