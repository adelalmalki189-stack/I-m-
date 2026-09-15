"""Shared Signal status-transition logic — the single choke point every
status change must pass through, per app/api/routes/signals.py's own
PATCH /signals/{id}/status docstring: alerting only fires on a transition
into ENTRY (docs/OPERATIONAL_SPEC_V0.1.md §6), and INVALIDATED is terminal
(docs/TESTING_SPEC_V0.1.md §3: "لا رجوع من INVALIDATED"). Extracted out of
the route handler so the live Scanner Loop (app/services/scanner_loop.py)
— which computes these transitions itself via app/engines/timing_engine.py
and must never re-derive or duplicate this policy — applies status changes
through the exact same function the HTTP API uses.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SignalStatus
from app.models.signals import Signal
from app.services.alerting_service import notify_signal_entry


class SignalTerminalError(Exception):
    """Raised when a caller attempts to move an INVALIDATED signal to any
    other status. INVALIDATED never reverts — a fresh Signal row must be
    created for a new attempt instead."""


async def apply_signal_status_transition(session: AsyncSession, signal: Signal, new_status: SignalStatus) -> None:
    if signal.status == SignalStatus.INVALIDATED:
        raise SignalTerminalError(f"signal {signal.signal_id} is INVALIDATED (terminal)")

    previous_status = signal.status
    signal.status = new_status
    await session.commit()

    if new_status == SignalStatus.ENTRY and previous_status != SignalStatus.ENTRY:
        await notify_signal_entry(session, signal)
