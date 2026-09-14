"""Circuit Breaker — docs/OPERATIONAL_SPEC_V0.1.md §3: once a primary data
source (IBKR) has gone longer than a threshold (default 60s) without a
successful update during trading hours, the whole system must flip to
DEGRADED MODE (stop new Signals, warn the Dashboard) rather than silently
keep evaluating breakouts on stale data.

Deliberately separate from GET /system/status's raw `provider.is_connected`
check (app/api/routes/system.py): a feed can report "connected" at the
socket level while still not having delivered an actual update in over a
minute (a stalled subscription, not a dropped socket) — this is the
"how long has it actually been stale" logic that endpoint's Phase 0 stub
explicitly deferred to a later phase.
"""
from __future__ import annotations

from datetime import datetime, timedelta

DEFAULT_OUTAGE_THRESHOLD_SECONDS = 60


def is_degraded(
    *,
    last_successful_update_at: datetime | None,
    now: datetime,
    outage_threshold_seconds: int = DEFAULT_OUTAGE_THRESHOLD_SECONDS,
) -> bool:
    """True once `now` is more than `outage_threshold_seconds` past the
    last successful update. `last_successful_update_at=None` (no update
    has ever been recorded) is also degraded -- a feed that never
    connected is never "healthy by default"."""
    if last_successful_update_at is None:
        return True
    return (now - last_successful_update_at) > timedelta(seconds=outage_threshold_seconds)
