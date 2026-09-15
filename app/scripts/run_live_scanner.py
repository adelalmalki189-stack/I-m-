"""Live Scanner Loop entrypoint — wires app/services/scanner_loop.py's
ScannerLoop to a real IBKR Paper connection and keeps the process alive.

This is the "future live scanner loop" every earlier phase's docstrings
pointed at ("a future live scanner loop", "Phase 6+ scanner loop", the
Feature Engine's own `prior_candles_for_continuity` note in
app/engines/feature_engine/pipeline.py): the component that actually
drives Setup/Timing/Contract/Risk/Scoring off live 5m candles instead of
a golden fixture, a Replay session, or a manual API call.

Usage:
    python -m app.scripts.run_live_scanner --symbol NVDA --symbol AMD
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from app.core.config import settings
from app.core.redis_client import get_redis
from app.data_providers.registry import create_provider
from app.services.scanner_loop import ScannerLoop

logger = logging.getLogger(__name__)

CALC_VERSION = "v0.1.0-live"


async def run(symbols: list[str]) -> None:
    # Importing this module is what registers "IBKR" as a production-allowed
    # provider name (app/data_providers/ibkr_provider.py).
    import app.data_providers.ibkr_provider  # noqa: F401

    provider = create_provider("IBKR")
    await provider.connect()

    loop = ScannerLoop(
        provider=provider,
        redis_client=get_redis(),
        tracked_symbols=symbols,
        calc_version=CALC_VERSION,
    )
    await loop.start()

    logger.info("scanner loop running for %s (+ SPY/QQQ context) -- ctrl-c to stop", symbols)
    while True:
        await asyncio.sleep(3600)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", dest="symbols", action="append", required=True, help="repeatable")
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()
    logger.info("connecting to IBKR at %s:%s", settings.ibkr_host, settings.ibkr_port)
    asyncio.run(run(args.symbols))
