"""docs/DATA_SPEC_V0.1.md §11 — signals."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid
from app.models.enums import EventSeverity, SignalDirection, SignalStatus


class Signal(Base):
    __tablename__ = "signals"

    signal_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(10), ForeignKey("instruments.symbol"), nullable=False)
    direction: Mapped[SignalDirection] = mapped_column(Enum(SignalDirection, native_enum=False), nullable=False)

    setup_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    contract_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    final_score: Mapped[float | None] = mapped_column(Numeric(5, 2))

    status: Mapped[SignalStatus] = mapped_column(
        Enum(SignalStatus, native_enum=False), nullable=False, default=SignalStatus.WAIT
    )

    entry_zone_low: Mapped[float | None] = mapped_column(Numeric(12, 4))
    entry_zone_high: Mapped[float | None] = mapped_column(Numeric(12, 4))
    invalidation: Mapped[float | None] = mapped_column(Numeric(12, 4))
    target: Mapped[float | None] = mapped_column(Numeric(12, 4))
    risk_reward: Mapped[float | None] = mapped_column(Numeric(5, 2))
    event_risk: Mapped[EventSeverity | None] = mapped_column(Enum(EventSeverity, native_enum=False))

    calc_version: Mapped[str] = mapped_column(
        String(10), ForeignKey("strategy_parameters.version"), nullable=False
    )
