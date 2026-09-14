"""Phase 0 completion check (docs/IMPLEMENTATION_PLAN_V0.1.md):

"النظام يستقبل شموع 5 دقائق حقيقية من IBKR Paper ويخزّنها في candles_5m
بشكل صحيح."

Connects to IBKR Paper (settings.ibkr_port default 7497), backfills one
day of historical 5m candles for a symbol, then stays subscribed and
persists every newly-closed live candle. No indicator math lives here —
that starts in Phase 1 (Feature Engine); this script only proves the
DataProvider -> Postgres path end to end.

Usage:
    python -m app.scripts.run_candle_ingestion --symbol SPY
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.core.database import get_session
from app.data_providers.registry import create_provider
from app.data_providers.schemas import Candle
from app.models.candles import Candle5m
from app.models.instruments import Instrument

logger = logging.getLogger(__name__)


async def _ensure_instrument(symbol: str) -> None:
    async with get_session() as session:
        await session.merge(Instrument(symbol=symbol, exchange="SMART", is_active=True))
        await session.commit()


async def _store_candle(candle: Candle) -> None:
    if not candle.is_complete:
        return  # never persist a still-forming bar — see CALCULATION_SPEC_V0.1.md
    async with get_session() as session:
        await session.merge(
            Candle5m(
                symbol=candle.symbol,
                timestamp=candle.timestamp,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                vwap=candle.vwap,
                is_complete=True,
            )
        )
        await session.commit()
    logger.info("stored candle %s %s close=%s", candle.symbol, candle.timestamp, candle.close)


async def run(symbol: str) -> None:
    # "IBKR" is registered as a production-allowed provider name in
    # app/data_providers/ibkr_provider.py — importing that module here is
    # what triggers the registration.
    import app.data_providers.ibkr_provider  # noqa: F401

    provider = create_provider("IBKR")
    await provider.connect()
    await _ensure_instrument(symbol)

    end = datetime.now(UTC)
    start = end - timedelta(days=1)
    backfill = await provider.get_historical_candles_5m(symbol, start, end)
    for candle in backfill:
        await _store_candle(candle)
    logger.info("backfilled %d candles for %s", len(backfill), symbol)

    await provider.subscribe_candles_5m(symbol, _store_candle)
    logger.info("subscribed to live 5m candles for %s — running until interrupted", symbol)
    while True:
        await asyncio.sleep(3600)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY")
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()
    asyncio.run(run(args.symbol))
