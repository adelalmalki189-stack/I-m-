"""docs/OPERATIONAL_SPEC_V0.1.md §1 — trading_calendar."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import SessionType


class TradingCalendarDay(Base):
    __tablename__ = "trading_calendar"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    session_type: Mapped[SessionType] = mapped_column(Enum(SessionType, native_enum=False), nullable=False)
    market_open: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    market_close: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(50), nullable=False)
