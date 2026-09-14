"""docs/API_SPEC_V0.1.md §9 — WS /ws/live-updates.

Exists because polling alone is too slow for a status that can be
short-lived (ENTRY, per Methodology §8). `publish_*` helpers are the hook
a future live scanner loop (Phase 6+) calls; this module owns only the
connection bookkeeping and message shapes, never signal/market logic.
"""
from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["websocket"])


class WebSocketLike(Protocol):
    async def accept(self) -> None: ...
    async def send_json(self, data: Any) -> None: ...


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[WebSocketLike] = []

    async def connect(self, websocket: WebSocketLike) -> None:
        await websocket.accept()
        self._connections.append(websocket)

    def disconnect(self, websocket: WebSocketLike) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        """Best-effort: a connection that fails to receive is dropped
        rather than taking down the whole broadcast for everyone else."""
        stale: list[WebSocketLike] = []
        for connection in self._connections:
            try:
                await connection.send_json(message)
            except Exception:  # noqa: BLE001 — any send failure means a dead connection
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


manager = ConnectionManager()


@router.websocket("/ws/live-updates")
async def live_updates(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def publish_signal_status_change(signal_id: str, new_status: str) -> None:
    await manager.broadcast({"type": "SIGNAL_STATUS_CHANGE", "signal_id": signal_id, "new_status": new_status})


async def publish_market_context_update(overall_market_regime: str) -> None:
    await manager.broadcast(
        {"type": "MARKET_CONTEXT_UPDATE", "overall_market_regime": overall_market_regime}
    )


async def publish_system_degraded(degraded_mode: bool) -> None:
    await manager.broadcast({"type": "SYSTEM_DEGRADED", "degraded_mode": degraded_mode})
