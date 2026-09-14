"""Populate `trading_calendar` from the NYSE calendar.

docs/OPERATIONAL_SPEC_V0.1.md §1: every time-based calculation in the
system (RVOL baseline day selection, session boundaries, End-of-session
rule) must consult this table instead of assuming every day looks alike.

Only trading days (FULL/HALF) are written — a date's absence from the
table already means CLOSED, so there is no need to enumerate weekends and
holidays explicitly.

Usage:
    python -m app.scripts.populate_trading_calendar --start 2025-01-01 --end 2026-12-31
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date

from app.core.database import get_session
from app.models.enums import SessionType
from app.models.trading_calendar import TradingCalendarDay

SOURCE = "pandas_market_calendars:NYSE"


async def populate_trading_calendar(start: date, end: date) -> int:
    import pandas_market_calendars as mcal

    nyse = mcal.get_calendar("NYSE")
    schedule = nyse.schedule(start_date=start, end_date=end)
    early_close_dates = {ts.date() for ts in nyse.early_closes(schedule).index}

    count = 0
    async with get_session() as session:
        for ts, row in schedule.iterrows():
            day = ts.date()
            session_type = SessionType.HALF if day in early_close_dates else SessionType.FULL
            await session.merge(
                TradingCalendarDay(
                    date=day,
                    session_type=session_type,
                    market_open=row["market_open"].to_pydatetime(),
                    market_close=row["market_close"].to_pydatetime(),
                    source=SOURCE,
                )
            )
            count += 1
        await session.commit()
    return count


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    inserted = asyncio.run(populate_trading_calendar(args.start, args.end))
    print(f"Upserted {inserted} trading_calendar rows ({args.start} .. {args.end}).")
