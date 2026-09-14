"""Timing Engine — Methodology §8: exactly four states, WAIT/READY/ENTRY/INVALIDATED.

A pure function of "the current candle's evaluation" plus "the signal's
previous status" — no hidden state, so every transition is independently
testable (docs/IMPLEMENTATION_PLAN_V0.1.md Phase 2: "اختبارات الانتقال
الحدّية").
"""
from __future__ import annotations

from decimal import Decimal

from app.engines.setup_engine import EntryZone
from app.models.enums import SignalDirection, SignalStatus


def determine_status(
    *,
    direction: SignalDirection,
    is_pre_breakout_ok: bool,
    breakout_confirmed: bool,
    breakout_level: Decimal,
    close: Decimal,
    entry_zone: EntryZone,
    previous_status: SignalStatus | None,
) -> SignalStatus:
    """Methodology §8:

    WAIT         — conditions not (yet, or no longer) aligned.
    READY        — pre-breakout conditions hold, breakout not confirmed yet.
    ENTRY        — breakout confirmed AND price still within the acceptable
                   entry zone (docs/CALCULATION_SPEC_V0.1.md §7 — beyond
                   max_extension is a miss, not a late entry).
    INVALIDATED  — terminal. Reached from READY/ENTRY when either the
                   underlying closes back through the original breakout
                   level (§8 worked example) or the pre-breakout conditions
                   stop holding. Never reverts to any other status once
                   set — a fresh Signal must be created for a new attempt
                   (docs/TESTING_SPEC_V0.1.md §3: "لا رجوع من INVALIDATED").
    """
    if previous_status == SignalStatus.INVALIDATED:
        return SignalStatus.INVALIDATED

    broke_back = (
        close < breakout_level if direction == SignalDirection.CALL else close > breakout_level
    )
    if previous_status in (SignalStatus.READY, SignalStatus.ENTRY) and (broke_back or not is_pre_breakout_ok):
        return SignalStatus.INVALIDATED

    if not is_pre_breakout_ok:
        return SignalStatus.WAIT

    if not breakout_confirmed:
        return SignalStatus.READY

    within_entry_zone = entry_zone.acceptable_min <= close <= entry_zone.acceptable_max
    return SignalStatus.ENTRY if within_entry_zone else SignalStatus.WAIT
