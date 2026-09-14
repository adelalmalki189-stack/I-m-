"""docs/DATA_SPEC_V0.1.md §4 — technical_features.

Deliberately a separate table from candles_5m: "كل Feature يجب أن يكون
قابلاً لإعادة الحساب" — every derived indicator must be inspectable on its
own, not folded silently into `signal = bullish`.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, ForeignKeyConstraint, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import MarketStructureState, TrendSlope


class TechnicalFeatures(Base):
    __tablename__ = "technical_features"
    __table_args__ = (
        ForeignKeyConstraint(["symbol", "timestamp"], ["candles_5m.symbol", "candles_5m.timestamp"]),
    )

    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    ema20: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    ema50: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    ema20_slope: Mapped[TrendSlope | None] = mapped_column(Enum(TrendSlope, native_enum=False))

    # nullable: docs/CALCULATION_SPEC_V0.1.md §3.3 — INVALID during RVOL warm-up
    rvol: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))

    atr14: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))

    pdh: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    pdl: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    day_high: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    day_low: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))

    swing_high: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    swing_low: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    swing_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    market_structure: Mapped[MarketStructureState | None] = mapped_column(
        Enum(MarketStructureState, native_enum=False)
    )

    # docs/DATA_SPEC_V0.1.md §10 — every derived row must trace to a
    # strategy_parameters version, or later audits can't tell which formula
    # produced this number.
    calc_version: Mapped[str] = mapped_column(
        String(10), ForeignKey("strategy_parameters.version"), nullable=False
    )
