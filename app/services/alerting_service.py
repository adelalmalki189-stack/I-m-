"""Alerting Service — docs/OPERATIONAL_SPEC_V0.1.md §6.

"تنبيه يُرسَل فقط عند انتقال Signal إلى حالة ENTRY (وليس عند WAIT/READY
لتفادي إغراق المستخدم بإشعارات)." PUSH here means the WebSocket channel
built in Phase 5 (app/api/ws.py) — a connected dashboard tab renders it as
a push notification / sound, which is the V0.1 "PUSH + SOUND" minimum
from the Operational Spec. Every attempt is logged to NotificationLog
regardless of delivery outcome, per docs/DATA_SPEC_V0.1.md §17.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws import publish_signal_status_change
from app.models.enums import DeliveryStatus, NotificationChannel
from app.models.notifications import NotificationLog
from app.models.signals import Signal


async def notify_signal_entry(session: AsyncSession, signal: Signal) -> NotificationLog:
    delivery_status = DeliveryStatus.SENT
    try:
        await publish_signal_status_change(signal.signal_id, signal.status.value)
    except Exception:  # noqa: BLE001 — a broadcast failure must still be logged, not raised
        delivery_status = DeliveryStatus.FAILED

    notification = NotificationLog(
        signal_id=signal.signal_id,
        channel=NotificationChannel.PUSH,
        sent_at=datetime.now(UTC),
        delivery_status=delivery_status,
    )
    session.add(notification)
    await session.commit()
    return notification
