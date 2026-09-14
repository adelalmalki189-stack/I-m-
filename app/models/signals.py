"""docs/DATA_SPEC_V0.1.md §11 — signals."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid
from app.models.enums import EventSeverity, SignalDirection, SignalStatus


class Signal(Base):
    __tablename__ = "signals"

    signal_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(10), ForeignKey("instruments.symbol"), nullable=False)
    direction: Mapped[SignalDirection] = mapped_column(Enum(SignalDirection, native_enum=False), nullable=False)

    setup_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    contract_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    final_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    status: Mapped[SignalStatus] = mapped_column(
        Enum(SignalStatus, native_enum=False), nullable=False, default=SignalStatus.WAIT
    )

    entry_zone_low: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    entry_zone_high: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    invalidation: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    target: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    risk_reward: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    event_risk: Mapped[EventSeverity | None] = mapped_column(Enum(EventSeverity, native_enum=False))

    # Added in Phase 5 (docs/IMPLEMENTATION_PLAN_V0.1.md, API_SPEC_V0.1.md
    # §3 "why this trade"): frozen condition-by-condition and Hard-Gate
    # results from the moment of decision, per Methodology §6/§24.
    # Without these, a "why" endpoint would have to either re-run the
    # engines against current (possibly-changed) data — a Hindsight risk
    # the Data Spec explicitly designed TradeSnapshot to avoid — or fake
    # the answer. Both null until the deciding engines have actually run.
    conditions: Mapped[list | None] = mapped_column(JSON)
    hard_gates: Mapped[dict | None] = mapped_column(JSON)

    calc_version: Mapped[str] = mapped_column(
        String(10), ForeignKey("strategy_parameters.version"), nullable=False
    )
