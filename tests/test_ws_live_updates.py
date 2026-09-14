from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.ws import ConnectionManager
from app.main import app


class _FakeWebSocket:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.accepted = False
        self.sent: list[dict] = []
        self._fail_send = fail_send

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict) -> None:
        if self._fail_send:
            raise RuntimeError("connection closed")
        self.sent.append(data)


async def test_connect_accepts_and_registers():
    manager = ConnectionManager()
    ws = _FakeWebSocket()
    await manager.connect(ws)
    assert ws.accepted is True
    assert ws in manager._connections  # noqa: SLF001 — testing internal bookkeeping directly


async def test_broadcast_delivers_to_all_connections():
    manager = ConnectionManager()
    ws1, ws2 = _FakeWebSocket(), _FakeWebSocket()
    await manager.connect(ws1)
    await manager.connect(ws2)

    await manager.broadcast({"type": "SIGNAL_STATUS_CHANGE", "signal_id": "abc", "new_status": "ENTRY"})

    assert ws1.sent == [{"type": "SIGNAL_STATUS_CHANGE", "signal_id": "abc", "new_status": "ENTRY"}]
    assert ws2.sent == ws1.sent


async def test_broadcast_drops_stale_connection_without_failing_others():
    manager = ConnectionManager()
    dead = _FakeWebSocket(fail_send=True)
    alive = _FakeWebSocket()
    await manager.connect(dead)
    await manager.connect(alive)

    await manager.broadcast({"type": "SYSTEM_DEGRADED", "degraded_mode": True})

    assert alive.sent == [{"type": "SYSTEM_DEGRADED", "degraded_mode": True}]
    assert dead not in manager._connections  # noqa: SLF001


async def test_disconnect_removes_connection():
    manager = ConnectionManager()
    ws = _FakeWebSocket()
    await manager.connect(ws)
    manager.disconnect(ws)
    assert ws not in manager._connections  # noqa: SLF001


def test_websocket_route_accepts_a_real_connection():
    with TestClient(app) as client, client.websocket_connect("/ws/live-updates"):
        pass  # reaching here without an exception proves accept() succeeded
