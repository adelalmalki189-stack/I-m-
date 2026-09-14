"""docs/DATA_SPEC_V0.1.md §17 / docs/OPERATIONAL_SPEC_V0.1.md §6 — notification_log."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid
from app.models.enums import DeliveryStatus, NotificationChannel


class NotificationLog(Base):
    __tablename__ = "notification_log"

    notification_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    signal_id: Mapped[str] = mapped_column(String(36), ForeignKey("signals.signal_id"), nullable=False)
    channel: Mapped[NotificationChannel] = mapped_column(
        Enum(NotificationChannel, native_enum=False), nullable=False
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivery_status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, native_enum=False), nullable=False
    )
