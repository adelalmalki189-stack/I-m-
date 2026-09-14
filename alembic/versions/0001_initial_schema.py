"""Initial schema — every table in docs/DATA_SPEC_V0.1.md

Revision ID: 0001
Revises:
Create Date: 2026-09-14

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_parameters",
        sa.Column("version", sa.String(10), primary_key=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "trading_calendar",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column(
            "session_type", sa.Enum("FULL", "HALF", "CLOSED", name="session_type", native_enum=False),
            nullable=False,
        ),
        sa.Column("market_open", sa.DateTime(timezone=True), nullable=True),
        sa.Column("market_close", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(50), nullable=False),
    )

    op.create_table(
        "instruments",
        sa.Column("symbol", sa.String(10), primary_key=True),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("sector", sa.String(50), nullable=True),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "candles_5m",
        sa.Column("symbol", sa.String(10), sa.ForeignKey("instruments.symbol"), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("open", sa.Numeric(12, 4), nullable=False),
        sa.Column("high", sa.Numeric(12, 4), nullable=False),
        sa.Column("low", sa.Numeric(12, 4), nullable=False),
        sa.Column("close", sa.Numeric(12, 4), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("vwap", sa.Numeric(12, 4), nullable=False),
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "technical_features",
        sa.Column("symbol", sa.String(10), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("ema20", sa.Numeric(12, 4), nullable=True),
        sa.Column("ema50", sa.Numeric(12, 4), nullable=True),
        sa.Column(
            "ema20_slope",
            sa.Enum("POSITIVE", "NEGATIVE", "FLAT", name="trend_slope", native_enum=False),
            nullable=True,
        ),
        sa.Column("rvol", sa.Numeric(6, 3), nullable=True),
        sa.Column("atr14", sa.Numeric(12, 4), nullable=True),
        sa.Column("pdh", sa.Numeric(12, 4), nullable=True),
        sa.Column("pdl", sa.Numeric(12, 4), nullable=True),
        sa.Column("day_high", sa.Numeric(12, 4), nullable=True),
        sa.Column("day_low", sa.Numeric(12, 4), nullable=True),
        sa.Column("swing_high", sa.Numeric(12, 4), nullable=True),
        sa.Column("swing_low", sa.Numeric(12, 4), nullable=True),
        sa.Column("swing_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "market_structure",
            sa.Enum("BULLISH", "BEARISH", "NEUTRAL", name="market_structure_state", native_enum=False),
            nullable=True,
        ),
        sa.Column("calc_version", sa.String(10), sa.ForeignKey("strategy_parameters.version"), nullable=False),
        sa.ForeignKeyConstraint(["symbol", "timestamp"], ["candles_5m.symbol", "candles_5m.timestamp"]),
    )

    op.create_table(
        "options_contracts",
        sa.Column("contract_id", sa.String(50), primary_key=True),
        sa.Column("symbol", sa.String(10), sa.ForeignKey("instruments.symbol"), nullable=False),
        sa.Column(
            "option_type", sa.Enum("CALL", "PUT", name="option_type", native_enum=False), nullable=False
        ),
        sa.Column("strike", sa.Numeric(12, 4), nullable=False),
        sa.Column("expiration", sa.Date(), nullable=False),
        sa.Column("dte", sa.Integer(), nullable=False),
        sa.Column("stale_after_split", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "market_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("symbol", sa.String(10), sa.ForeignKey("instruments.symbol"), nullable=True),
        sa.Column(
            "event_type",
            sa.Enum(
                "EARNINGS", "CPI", "FOMC", "NFP", "FED_SPEECH", "ECONOMIC_DATA",
                name="event_type", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "severity",
            sa.Enum("LOW", "MEDIUM", "HIGH", "EXTREME", name="event_severity", native_enum=False),
            nullable=False,
        ),
        sa.Column("source", sa.String(50), nullable=False),
    )

    op.create_table(
        "signals",
        sa.Column("signal_id", sa.String(36), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(10), sa.ForeignKey("instruments.symbol"), nullable=False),
        sa.Column(
            "direction", sa.Enum("CALL", "PUT", name="signal_direction", native_enum=False), nullable=False
        ),
        sa.Column("setup_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("contract_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("final_score", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "WAIT", "READY", "ENTRY", "INVALIDATED", "EXPIRED",
                name="signal_status", native_enum=False,
            ),
            nullable=False,
            server_default="WAIT",
        ),
        sa.Column("entry_zone_low", sa.Numeric(12, 4), nullable=True),
        sa.Column("entry_zone_high", sa.Numeric(12, 4), nullable=True),
        sa.Column("invalidation", sa.Numeric(12, 4), nullable=True),
        sa.Column("target", sa.Numeric(12, 4), nullable=True),
        sa.Column("risk_reward", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "event_risk",
            sa.Enum("LOW", "MEDIUM", "HIGH", "EXTREME", name="event_severity", native_enum=False),
            nullable=True,
        ),
        sa.Column("calc_version", sa.String(10), sa.ForeignKey("strategy_parameters.version"), nullable=False),
    )

    op.create_table(
        "signal_snapshots",
        sa.Column("signal_id", sa.String(36), sa.ForeignKey("signals.signal_id"), primary_key=True),
        sa.Column("underlying_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("vwap", sa.Numeric(12, 4), nullable=False),
        sa.Column("ema20", sa.Numeric(12, 4), nullable=False),
        sa.Column("ema50", sa.Numeric(12, 4), nullable=False),
        sa.Column("rvol", sa.Numeric(6, 3), nullable=True),
        sa.Column("spy_state", sa.String(20), nullable=True),
        sa.Column("qqq_state", sa.String(20), nullable=True),
        sa.Column("vix_value", sa.Numeric(6, 2), nullable=True),
        sa.Column("sector_state", sa.String(20), nullable=True),
        sa.Column("contract_id", sa.String(50), nullable=True),
        sa.Column("strike", sa.Numeric(12, 4), nullable=True),
        sa.Column("expiration", sa.Date(), nullable=True),
        sa.Column("dte", sa.Integer(), nullable=True),
        sa.Column("bid", sa.Numeric(12, 4), nullable=True),
        sa.Column("ask", sa.Numeric(12, 4), nullable=True),
        sa.Column("last", sa.Numeric(12, 4), nullable=True),
        sa.Column("volume", sa.Integer(), nullable=True),
        sa.Column("oi", sa.Integer(), nullable=True),
        sa.Column("iv", sa.Numeric(6, 4), nullable=True),
        sa.Column("delta", sa.Numeric(8, 5), nullable=True),
        sa.Column("calc_version", sa.String(10), sa.ForeignKey("strategy_parameters.version"), nullable=False),
    )

    op.create_table(
        "risk_plans",
        sa.Column("signal_id", sa.String(36), sa.ForeignKey("signals.signal_id"), primary_key=True),
        sa.Column("account_size", sa.Numeric(14, 2), nullable=False),
        sa.Column("risk_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("max_risk", sa.Numeric(14, 2), nullable=False),
        sa.Column("entry_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("stop_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("target_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("risk_per_contract", sa.Numeric(14, 2), nullable=False),
        sa.Column("recommended_quantity", sa.Integer(), nullable=False),
        sa.Column("max_loss", sa.Numeric(14, 2), nullable=False),
        sa.Column("potential_profit", sa.Numeric(14, 2), nullable=False),
        sa.Column("r_multiple", sa.Numeric(5, 2), nullable=False),
    )

    op.create_table(
        "portfolio_risk_config",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("max_concurrent_signals", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("max_concurrent_per_sector", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("daily_max_loss_pct", sa.Numeric(5, 2), nullable=False, server_default="3.0"),
        sa.Column("max_total_deployed_risk_pct", sa.Numeric(5, 2), nullable=False, server_default="5.0"),
        sa.Column("enforce_pdt_limit", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "trades",
        sa.Column("trade_id", sa.String(36), primary_key=True),
        sa.Column("signal_id", sa.String(36), sa.ForeignKey("signals.signal_id"), nullable=False),
        sa.Column("user_entry_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_entry_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("user_exit_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_exit_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(14, 2), nullable=True),
        sa.Column("max_profit", sa.Numeric(14, 2), nullable=True),
        sa.Column("max_loss", sa.Numeric(14, 2), nullable=True),
        sa.Column(
            "exit_reason",
            sa.Enum(
                "STOP_HIT", "TARGET_HIT", "UNDERLYING_INVALIDATION", "VWAP_FAILURE",
                "MOMENTUM_FAILURE", "BREAKOUT_FAILURE", "TIME_STOP",
                "EVENT_RISK_ESCALATION", "END_OF_SESSION",
                name="exit_reason", native_enum=False,
            ),
            nullable=True,
        ),
    )

    op.create_table(
        "outcomes",
        sa.Column("signal_id", sa.String(36), sa.ForeignKey("signals.signal_id"), primary_key=True),
        sa.Column("offset_minutes", sa.Integer(), primary_key=True),
        sa.Column("underlying_price_at_offset", sa.Numeric(12, 4), nullable=False),
        sa.Column("option_price_at_offset", sa.Numeric(12, 4), nullable=True),
        sa.Column("mfe", sa.Numeric(12, 4), nullable=True),
        sa.Column("mae", sa.Numeric(12, 4), nullable=True),
    )

    op.create_table(
        "notification_log",
        sa.Column("notification_id", sa.String(36), primary_key=True),
        sa.Column("signal_id", sa.String(36), sa.ForeignKey("signals.signal_id"), nullable=False),
        sa.Column(
            "channel",
            sa.Enum("PUSH", "SOUND", "WEBHOOK", "SMS", name="notification_channel", native_enum=False),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "delivery_status",
            sa.Enum("SENT", "FAILED", name="delivery_status", native_enum=False),
            nullable=False,
        ),
    )

    op.create_table(
        "iv_history",
        sa.Column("symbol", sa.String(10), sa.ForeignKey("instruments.symbol"), primary_key=True),
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("atm_iv", sa.Numeric(6, 4), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("iv_history")
    op.drop_table("notification_log")
    op.drop_table("outcomes")
    op.drop_table("trades")
    op.drop_table("portfolio_risk_config")
    op.drop_table("risk_plans")
    op.drop_table("signal_snapshots")
    op.drop_table("signals")
    op.drop_table("market_events")
    op.drop_table("options_contracts")
    op.drop_table("technical_features")
    op.drop_table("candles_5m")
    op.drop_table("instruments")
    op.drop_table("trading_calendar")
    op.drop_table("strategy_parameters")
