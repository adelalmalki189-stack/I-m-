"""Unit tests for the session-aware candle history queries
(app/services/candle_history.py) backing the live Scanner Loop.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.models.candles import Candle5m
from app.models.enums import SessionType
from app.models.trading_calendar import TradingCalendarDay
from app.services.candle_history import (
    get_baseline_sessions,
    get_continuity_candles,
    get_previous_session_candles,
    get_session_candles,
    to_candle_dto,
)

SYMBOL = "TEST"


def _session_open(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 9, 30, tzinfo=UTC)


def _session_close(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 16, 0, tzinfo=UTC)


async def _seed_day(
    db_session, day: date, *, session_type: SessionType = SessionType.FULL, n_bars: int = 3
) -> None:
    db_session.add(
        TradingCalendarDay(
            date=day, session_type=session_type, market_open=_session_open(day),
            market_close=_session_close(day), source="TEST",
        )
    )
    for i in range(n_bars):
        db_session.add(
            Candle5m(
                symbol=SYMBOL, timestamp=_session_open(day) + timedelta(minutes=5 * i),
                open=Decimal(100), high=Decimal(101), low=Decimal(99), close=Decimal(100),
                volume=1000, vwap=Decimal(100), is_complete=True,
            )
        )
    await db_session.commit()


async def test_get_session_candles_returns_only_that_days_bars(db_session):
    day1, day2 = date(2026, 3, 2), date(2026, 3, 3)
    await _seed_day(db_session, day1, n_bars=3)
    await _seed_day(db_session, day2, n_bars=2)

    candles = await get_session_candles(db_session, SYMBOL, day2)

    assert len(candles) == 2
    assert all(c.timestamp.date() == day2 for c in candles)


async def test_get_session_candles_returns_empty_for_a_day_absent_from_the_calendar(db_session):
    # No TradingCalendarDay row at all -> CLOSED per docs/OPERATIONAL_SPEC_V0.1.md §1.
    candles = await get_session_candles(db_session, SYMBOL, date(2099, 1, 1))
    assert candles == []


async def test_get_previous_session_candles_skips_weekends_and_finds_the_prior_trading_day(db_session):
    friday, monday = date(2026, 3, 6), date(2026, 3, 9)  # Fri -> Mon, no calendar row for the weekend
    await _seed_day(db_session, friday, n_bars=4)
    await _seed_day(db_session, monday, n_bars=1)

    previous = await get_previous_session_candles(db_session, SYMBOL, monday)

    assert previous is not None
    assert len(previous) == 4
    assert previous[0].timestamp.date() == friday


async def test_get_previous_session_candles_returns_none_with_no_prior_history(db_session):
    only_day = date(2026, 3, 2)
    await _seed_day(db_session, only_day, n_bars=1)

    previous = await get_previous_session_candles(db_session, SYMBOL, only_day)

    assert previous is None


async def test_get_baseline_sessions_excludes_half_days(db_session):
    base = date(2026, 3, 2)
    full_day = base
    half_day = base + timedelta(days=1)
    target_day = base + timedelta(days=2)
    await _seed_day(db_session, full_day, session_type=SessionType.FULL, n_bars=2)
    await _seed_day(db_session, half_day, session_type=SessionType.HALF, n_bars=2)

    baseline = await get_baseline_sessions(db_session, SYMBOL, target_day, min_valid_days=20)

    assert len(baseline) == 1  # only the FULL day counts
    assert baseline[0][0].timestamp.date() == full_day


async def test_get_baseline_sessions_respects_min_valid_days_limit(db_session):
    target_day = date(2026, 4, 1)
    for offset in range(1, 6):  # 5 prior FULL days
        await _seed_day(db_session, target_day - timedelta(days=offset), n_bars=1)

    baseline = await get_baseline_sessions(db_session, SYMBOL, target_day, min_valid_days=3)

    assert len(baseline) == 3


async def test_get_continuity_candles_includes_half_days_and_is_chronological(db_session):
    base = date(2026, 3, 2)
    day1, day2, target = base, base + timedelta(days=1), base + timedelta(days=2)
    await _seed_day(db_session, day1, session_type=SessionType.FULL, n_bars=2)
    await _seed_day(db_session, day2, session_type=SessionType.HALF, n_bars=2)

    continuity = await get_continuity_candles(db_session, SYMBOL, target, lookback_sessions=10)

    assert len(continuity) == 4  # both days included, HALF is not excluded here
    timestamps = [c.timestamp for c in continuity]
    assert timestamps == sorted(timestamps)


def test_to_candle_dto_maps_every_field():
    row = Candle5m(
        symbol="X", timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=UTC), open=Decimal(1), high=Decimal(2),
        low=Decimal(0), close=Decimal("1.5"), volume=500, vwap=Decimal("1.4"), is_complete=True,
    )
    dto = to_candle_dto(row)

    assert dto.symbol == "X"
    assert dto.close == Decimal("1.5")
    assert dto.is_complete is True
    assert dto.source == "db"
