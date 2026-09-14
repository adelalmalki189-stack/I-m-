"""docs/API_SPEC_V0.1.md §8 — GET /system/status.

Phase 0 stub: reflects whatever DataProvider is attached to app.state
(none, in a plain `uvicorn app.main:app` run before Phase 1 wires the
live scanner loop in). Degraded-mode detection itself is implemented here
so later phases only need to set `request.app.state.data_provider`.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

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
    return SystemStatus(
        data_feed_status="OK" if connected else "DOWN",
        # docs/OPERATIONAL_SPEC_V0.1.md §3 circuit breaker: Phase 0 only
        # reports raw connection state; the 60s-outage threshold is applied
        # by the scanner loop introduced in later phases.
        degraded_mode=not connected,
        last_scan_at=getattr(request.app.state, "last_scan_at", None),
        last_scan_cycle_ms=getattr(request.app.state, "last_scan_cycle_ms", None),
    )


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}
