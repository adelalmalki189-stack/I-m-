"""Every table in docs/DATA_SPEC_V0.1.md must exist and be creatable."""
from __future__ import annotations

from app.models import Base

EXPECTED_TABLES = {
    "instruments",
    "candles_5m",
    "technical_features",
    "options_contracts",
    "market_events",
    "trading_calendar",
    "strategy_parameters",
    "signals",
    "signal_snapshots",
    "risk_plans",
    "portfolio_risk_config",
    "trades",
    "outcomes",
    "notification_log",
    "iv_history",
}


def test_all_data_spec_tables_are_registered():
    assert EXPECTED_TABLES.issubset(set(Base.metadata.tables))


async def test_create_all_succeeds(engine):
    # The `engine` fixture already ran create_all in conftest; reaching
    # here without an exception is the assertion.
    async with engine.connect():
        pass
