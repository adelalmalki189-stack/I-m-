"""Integration tests for GET /system/status — docs/TESTING_SPEC_V0.1.md §8's
pre-Paper-Trading checklist item "Circuit Breaker (فقدان بيانات) ... تم
اختبارهما يدوياً بمحاكاة حقيقية وليس فقط بالكود": these hit the real
endpoint through the real FastAPI app with a FakeDataProvider standing in
for IBKR, simulating an actual outage (disconnect / a stale
last_data_update_at) rather than only unit-testing is_degraded() in
isolation (that lives in tests/test_circuit_breaker.py). One test also
proves the SYSTEM_DEGRADED WebSocket broadcast that drives the Dashboard's
"⚠️ Data Feed Degraded" banner is actually delivered on a real connection,
not just enqueued.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.core.circuit_breaker import DEFAULT_OUTAGE_THRESHOLD_SECONDS
from app.main import app
from tests.fakes.fake_data_provider import FakeDataProvider


def _reset_system_state() -> None:
    for attr in ("data_provider", "last_data_update_at", "last_known_degraded_mode"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def test_no_provider_attached_reports_unknown_not_degraded():
    _reset_system_state()

    with TestClient(app) as client:
        response = client.get("/system/status")

    assert response.status_code == 200
    body = response.json()
    assert body["data_feed_status"] == "UNKNOWN"
    assert body["degraded_mode"] is False

    _reset_system_state()


def test_connected_provider_with_a_recent_update_is_not_degraded():
    _reset_system_state()
    provider = FakeDataProvider()
    app.state.data_provider = provider

    try:
        with TestClient(app) as client:
            asyncio.run(provider.connect())
            app.state.last_data_update_at = datetime.now(UTC)

            response = client.get("/system/status")

        assert response.status_code == 200
        body = response.json()
        assert body["data_feed_status"] == "OK"
        assert body["degraded_mode"] is False
    finally:
        _reset_system_state()


def test_connected_provider_that_never_sent_an_update_is_degraded():
    """A socket-connected provider that has never delivered a single
    candle/quote must not be reported healthy just because the socket is
    up — this is exactly the "stalled subscription" gap a raw
    is_connected check would miss."""
    _reset_system_state()
    provider = FakeDataProvider()
    app.state.data_provider = provider

    try:
        with TestClient(app) as client:
            asyncio.run(provider.connect())

            response = client.get("/system/status")

        assert response.status_code == 200
        body = response.json()
        assert body["degraded_mode"] is True
        assert body["data_feed_status"] == "DOWN"
    finally:
        _reset_system_state()


def test_simulated_real_outage_and_recovery_are_both_reflected():
    """The full circuit-breaker lifecycle against a simulated real outage:
    connected+fresh -> degraded once the feed goes stale/disconnects ->
    healthy again once it reconnects and delivers a fresh update."""
    _reset_system_state()
    provider = FakeDataProvider()
    app.state.data_provider = provider

    try:
        with TestClient(app) as client:
            asyncio.run(provider.connect())
            app.state.last_data_update_at = datetime.now(UTC)
            assert client.get("/system/status").json()["degraded_mode"] is False

            # Simulate the outage docs/OPERATIONAL_SPEC_V0.1.md §3 describes:
            # data stops arriving for longer than the threshold.
            app.state.last_data_update_at = datetime.now(UTC) - timedelta(
                seconds=DEFAULT_OUTAGE_THRESHOLD_SECONDS + 5
            )
            outage_response = client.get("/system/status").json()
            assert outage_response["degraded_mode"] is True
            assert outage_response["data_feed_status"] == "DOWN"

            # A hard disconnect is degraded too, regardless of the timestamp.
            asyncio.run(provider.disconnect())
            app.state.last_data_update_at = datetime.now(UTC)
            assert client.get("/system/status").json()["degraded_mode"] is True

            # Recovery: reconnect and deliver a fresh update.
            asyncio.run(provider.connect())
            app.state.last_data_update_at = datetime.now(UTC)
            recovered = client.get("/system/status").json()
            assert recovered["degraded_mode"] is False
            assert recovered["data_feed_status"] == "OK"
    finally:
        _reset_system_state()


def test_degraded_transition_is_broadcast_to_a_real_connected_client():
    """The Dashboard's "⚠️ Data Feed Degraded" banner (app/static/dashboard
    /index.html) toggles on a SYSTEM_DEGRADED WS message — this proves that
    message is actually delivered over a real WebSocket connection when a
    real (simulated) outage is detected, not merely that
    publish_system_degraded() exists as a function nothing calls."""
    _reset_system_state()
    provider = FakeDataProvider()
    app.state.data_provider = provider

    try:
        with TestClient(app) as client, client.websocket_connect("/ws/live-updates") as websocket:
            asyncio.run(provider.connect())
            app.state.last_data_update_at = datetime.now(UTC)
            client.get("/system/status")  # first-ever check: None -> False transition, broadcasts once
            first_message = websocket.receive_json()
            assert first_message == {"type": "SYSTEM_DEGRADED", "degraded_mode": False}

            app.state.last_data_update_at = datetime.now(UTC) - timedelta(
                seconds=DEFAULT_OUTAGE_THRESHOLD_SECONDS + 5
            )
            client.get("/system/status")  # healthy -> degraded transition

            message = websocket.receive_json()
            assert message == {"type": "SYSTEM_DEGRADED", "degraded_mode": True}
    finally:
        _reset_system_state()
