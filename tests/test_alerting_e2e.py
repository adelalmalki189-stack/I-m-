"""Phase 6 completion criterion — alerting half of it
(docs/IMPLEMENTATION_PLAN_V0.1.md): "تنبيه يصل فعلياً (اختبار end-to-end
وليس سجل قاعدة بيانات فقط)."

Uses TestClient's own websocket_connect + a synchronous client.patch call
in the same `with` block — the supported way to test WebSocket delivery
without an event-loop mismatch, since both run on TestClient's single
managed loop. A NotificationLog row alone would not prove delivery; only
actually reading the message off the socket does.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models.enums import DeliveryStatus, NotificationChannel, SignalDirection, SignalStatus
from app.models.instruments import Instrument
from app.models.notifications import NotificationLog
from app.models.signals import Signal
from app.models.strategy_parameters import StrategyParameters

CALC_VERSION = "v0.1.0-alerting-e2e"


async def _seed_ready_signal(db_session) -> Signal:
    db_session.add(Instrument(symbol="NVDA", exchange="SMART", is_active=True))
    db_session.add(
        StrategyParameters(version=CALC_VERSION, parameters={}, effective_from=datetime(2026, 3, 1, tzinfo=UTC))
    )
    await db_session.flush()
    signal = Signal(
        timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=UTC), symbol="NVDA", direction=SignalDirection.CALL,
        status=SignalStatus.READY, calc_version=CALC_VERSION,
    )
    db_session.add(signal)
    await db_session.commit()
    return signal


async def test_entry_transition_delivers_a_real_websocket_message(db_session):
    signal = await _seed_ready_signal(db_session)

    with TestClient(app) as client, client.websocket_connect("/ws/live-updates") as websocket:
        response = client.patch(f"/signals/{signal.signal_id}/status", json={"status": "ENTRY"})
        assert response.status_code == 200

        message = websocket.receive_json()
        assert message == {
            "type": "SIGNAL_STATUS_CHANGE",
            "signal_id": signal.signal_id,
            "new_status": "ENTRY",
        }

    log_result = await db_session.execute(
        select(NotificationLog).where(NotificationLog.signal_id == signal.signal_id)
    )
    logs = log_result.scalars().all()
    assert len(logs) == 1
    assert logs[0].channel == NotificationChannel.PUSH
    assert logs[0].delivery_status == DeliveryStatus.SENT


async def test_no_alert_fires_on_non_entry_transitions(db_session):
    signal = await _seed_ready_signal(db_session)

    with TestClient(app) as client:
        response = client.patch(f"/signals/{signal.signal_id}/status", json={"status": "WAIT"})
    assert response.status_code == 200

    log_result = await db_session.execute(
        select(NotificationLog).where(NotificationLog.signal_id == signal.signal_id)
    )
    assert log_result.scalars().all() == []


async def test_invalidated_status_is_terminal_via_the_api(db_session):
    signal = await _seed_ready_signal(db_session)

    with TestClient(app) as client:
        first = client.patch(f"/signals/{signal.signal_id}/status", json={"status": "INVALIDATED"})
        assert first.status_code == 200

        second = client.patch(f"/signals/{signal.signal_id}/status", json={"status": "ENTRY"})

    assert second.status_code == 409
    assert second.json()["detail"]["error_code"] == "SIGNAL_INVALIDATED_TERMINAL"
