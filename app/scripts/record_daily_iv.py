"""Daily ATM IV recording (docs/IMPLEMENTATION_PLAN_V0.1.md Phase 3: "يجب
أن يبدأ التجميع من هذه المرحلة حتى تتوفر 252 يوماً لاحقاً").

Without this running once per trading day per scanned symbol, IV Rank
(docs/CALCULATION_SPEC_V0.1.md §9.5) has no history to rank against and
Contract Engine's iv_score() falls back to its neutral default forever.

Usage:
    python -m app.scripts.record_daily_iv --symbol SPY
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime

from app.core.database import get_session
from app.data_providers.registry import create_provider
from app.engines.contract_engine import select_atm_contract
from app.models.enums import OptionType
from app.models.iv_history import IVHistory

logger = logging.getLogger(__name__)

SOURCE = "IBKR"


async def record_daily_iv(symbol: str) -> None:
    import app.data_providers.ibkr_provider  # noqa: F401 — registers "IBKR" with the provider registry

    provider = create_provider("IBKR")
    await provider.connect()

    underlying_quote = await provider.get_underlying_quote(symbol)
    chain = await provider.get_option_chain(symbol)
    atm_contract = select_atm_contract(chain, underlying_quote.last, option_type=OptionType.CALL)
    if atm_contract is None:
        logger.warning("no eligible ATM contract found for %s — skipping today's iv_history row", symbol)
        return

    quote = await provider.get_option_quote(atm_contract.contract_id)
    if quote.iv is None:
        logger.warning("ATM contract %s has no IV quote — skipping", atm_contract.contract_id)
        return

    async with get_session() as session:
        await session.merge(
            IVHistory(symbol=symbol, date=datetime.now(UTC).date(), atm_iv=quote.iv, source=SOURCE)
        )
        await session.commit()

    logger.info("recorded iv_history for %s: atm_iv=%s (contract=%s)", symbol, quote.iv, atm_contract.contract_id)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()
    asyncio.run(record_daily_iv(args.symbol))
