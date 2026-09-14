"""docs/API_SPEC_V0.1.md §8 — GET /system/status.

Reflects whatever DataProvider is attached to app.state (none, in a plain
`uvicorn app.main:app` run before a live scanner loop wires one in).
Degraded-mode detection applies the real docs/OPERATIONAL_SPEC_V0.1.md §3
Circuit Breaker (app/core/circuit_breaker.py): not just raw socket
connection state, but "no successful update in over
outage_threshold_seconds" — a feed can stay socket-connected while its
subscription has silently stalled, which `provider.is_connected` alone
would miss. A live scanner loop is expected to keep
`request.app.state.last_data_update_at` current on every successful
candle/quote; nothing in this codebase runs one yet (every phase so far
only reaches as far as the pure engines and the read/write API), so until
one exists this endpoint reports degraded based on whatever that
timestamp last held (default: never set -> always degraded, per
is_degraded's documented "no update ever recorded" case).

Also broadcasts SYSTEM_DEGRADED over the WS channel on every degraded/OK
transition it detects, so the Dashboard banner
(docs/OPERATIONAL_SPEC_V0.1.md §3: "⚠️ Data Feed Degraded") updates
without a manual refresh — the same `publish_system_degraded` hook
app/api/ws.py already exposed for "a future live scanner loop", called
here for now since no such loop exists yet in this codebase to call it on
its own schedule.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.api.ws import publish_system_degraded
from app.core.circuit_breaker import is_degraded

router = APIRouter(prefix="/system", tags=["system"])


class SystemStatus(BaseModel):
    data_feed_status: str
    degraded_mode: bool
    last_scan_cycle_ms: int | None = None
    last_scan_at: datetime | None = None


@router.get("/status", response_model=SystemStatus)
async def get_system_status(request: Request) -> SystemStatus:
    provider = getattr(request.app.state, "data_provider", None)

    if provider is None:
        return SystemStatus(data_feed_status="UNKNOWN", degraded_mode=False)

    connected = provider.is_connected
    last_data_update_at = getattr(request.app.state, "last_data_update_at", None)
    degraded = (not connected) or is_degraded(last_successful_update_at=last_data_update_at, now=datetime.now(UTC))

    previously_degraded = getattr(request.app.state, "last_known_degraded_mode", None)
    if degraded != previously_degraded:
        await publish_system_degraded(degraded)
    request.app.state.last_known_degraded_mode = degraded

    return SystemStatus(
        data_feed_status="OK" if not degraded else "DOWN",
        degraded_mode=degraded,
        last_scan_at=getattr(request.app.state, "last_scan_at", None),
        last_scan_cycle_ms=getattr(request.app.state, "last_scan_cycle_ms", None),
    )


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}
