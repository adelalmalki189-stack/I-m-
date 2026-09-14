"""Outcome Tracker — automatic MFE/MAE recording (Methodology §14).

docs/IMPLEMENTATION_PLAN_V0.1.md Phase 6: "بيانات outcome تتراكم تلقائياً
بدون تدخل يدوي" — meant to run on a schedule (e.g. every 5 minutes) rather
than be triggered by user action, the same pattern as
app/scripts/record_daily_iv.py.

Usage:
    python -m app.scripts.run_outcome_tracker
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.data_providers.schemas import Candle as CandleDTO
from app.engines.outcome_tracker import OUTCOME_OFFSETS_MINUTES, compute_outcome_at_offset
from app.models.candles import Candle5m
from app.models.enums import DataStatus
from app.models.outcomes import Outcome
from app.models.signal_snapshots import SignalSnapshot
from app.models.signals import Signal

logger = logging.getLogger(__name__)


def _to_dto(candle: Candle5m) -> CandleDTO:
    return CandleDTO(
        symbol=candle.symbol, timestamp=candle.timestamp, open=candle.open, high=candle.high,
        low=candle.low, close=candle.close, volume=candle.volume, vwap=candle.vwap,
        is_complete=candle.is_complete, source="db", data_status=DataStatus.VALID,
    )


async def record_outcomes_for_signal(
    session: AsyncSession, signal: Signal, *, now: datetime | None = None
) -> list[Outcome]:
    """Anchors MFE/MAE tracking to the signal's own timestamp and the
    underlying price frozen in its SignalSnapshot (docs/DATA_SPEC_V0.1.md
    §12) — never a live re-fetch, the same reasoning TradeSnapshot exists
    for in the first place. Idempotent: a checkpoint already recorded is
    never recomputed or duplicated."""
    now = now or datetime.now(UTC)

    snapshot = await session.get(SignalSnapshot, signal.signal_id)
    if snapshot is None:
        return []  # nothing to anchor to yet

    entry_time = signal.timestamp
    entry_price = snapshot.underlying_price

    created: list[Outcome] = []
    for offset in OUTCOME_OFFSETS_MINUTES:
        checkpoint_time = entry_time + timedelta(minutes=offset)
        if checkpoint_time > now:
            continue  # this checkpoint hasn't happened yet

        existing = await session.get(Outcome, {"signal_id": signal.signal_id, "offset_minutes": offset})
        if existing is not None:
            continue

        candles_result = await session.execute(
            select(Candle5m)
            .where(
                Candle5m.symbol == signal.symbol,
                Candle5m.timestamp >= entry_time,
                Candle5m.timestamp <= checkpoint_time,
                Candle5m.is_complete.is_(True),
            )
            .order_by(Candle5m.timestamp)
        )
        candles = candles_result.scalars().all()
        if not candles:
            continue

        outcome_result = compute_outcome_at_offset(
            direction=signal.direction,
            entry_price=entry_price,
            candles_since_entry=[_to_dto(c) for c in candles],
        )

        outcome = Outcome(
            signal_id=signal.signal_id,
            offset_minutes=offset,
            underlying_price_at_offset=outcome_result.price_at_offset,
            mfe=outcome_result.mfe,
            mae=outcome_result.mae,
        )
        session.add(outcome)
        created.append(outcome)

    if created:
        await session.commit()
    return created


async def run_outcome_tracker_once() -> int:
    """Scans every signal and records whatever checkpoints have newly
    elapsed — cheap no-op for signals that are already fully tracked or
    have nothing new to record."""
    total_created = 0
    async with get_session() as session:
        result = await session.execute(select(Signal))
        for signal in result.scalars().all():
            created = await record_outcomes_for_signal(session, signal)
            total_created += len(created)
    return total_created


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    argparse.ArgumentParser(description=__doc__).parse_args()
    count = asyncio.run(run_outcome_tracker_once())
    logger.info("recorded %d new outcome rows", count)
