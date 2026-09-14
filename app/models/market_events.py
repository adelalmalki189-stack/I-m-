"""docs/DATA_SPEC_V0.1.md §8 — market_events."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid
from app.models.enums import EventSeverity, EventType


class MarketEvent(Base):
    __tablename__ = "market_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    symbol: Mapped[str | None] = mapped_column(String(10), ForeignKey("instruments.symbol"))
    event_type: Mapped[EventType] = mapped_column(Enum(EventType, native_enum=False), nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[EventSeverity] = mapped_column(Enum(EventSeverity, native_enum=False), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
