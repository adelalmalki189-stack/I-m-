from __future__ import annotations

from decimal import Decimal

from app.engines.setup_engine import compute_entry_zone
from app.engines.timing_engine import determine_status
from app.models.enums import SignalDirection, SignalStatus

LEVEL = Decimal(100)
ZONE = compute_entry_zone(level=LEVEL, atr14=Decimal(2), direction=SignalDirection.CALL)


def test_wait_when_pre_breakout_conditions_not_aligned():
    status = determine_status(
        direction=SignalDirection.CALL, is_pre_breakout_ok=False, breakout_confirmed=False,
        breakout_level=LEVEL, close=Decimal(99), entry_zone=ZONE, previous_status=None,
    )
    assert status == SignalStatus.WAIT


def test_ready_when_pre_breakout_ok_but_no_breakout_yet():
    status = determine_status(
        direction=SignalDirection.CALL, is_pre_breakout_ok=True, breakout_confirmed=False,
        breakout_level=LEVEL, close=Decimal("99.90"), entry_zone=ZONE, previous_status=None,
    )
    assert status == SignalStatus.READY


def test_entry_when_breakout_confirmed_and_within_zone():
    status = determine_status(
        direction=SignalDirection.CALL, is_pre_breakout_ok=True, breakout_confirmed=True,
        breakout_level=LEVEL, close=Decimal("100.50"), entry_zone=ZONE, previous_status=SignalStatus.READY,
    )
    assert status == SignalStatus.ENTRY


def test_wait_not_entry_when_price_ran_past_max_extension():
    # docs/CALCULATION_SPEC_V0.1.md §7 Avoid Chasing: breakout confirmed but
    # price already beyond max_extension (102) -> WAIT, not a late ENTRY.
    status = determine_status(
        direction=SignalDirection.CALL, is_pre_breakout_ok=True, breakout_confirmed=True,
        breakout_level=LEVEL, close=Decimal("103.00"), entry_zone=ZONE, previous_status=SignalStatus.READY,
    )
    assert status == SignalStatus.WAIT


def test_invalidated_when_close_drops_back_below_breakout_level():
    status = determine_status(
        direction=SignalDirection.CALL, is_pre_breakout_ok=True, breakout_confirmed=False,
        breakout_level=LEVEL, close=Decimal("99.50"), entry_zone=ZONE, previous_status=SignalStatus.ENTRY,
    )
    assert status == SignalStatus.INVALIDATED


def test_close_exactly_at_breakout_level_is_not_invalidated():
    # Edge case from docs/TESTING_SPEC_V0.1.md §3: exactly-equal close must
    # not trigger invalidation (it isn't literally *below* the level).
    status = determine_status(
        direction=SignalDirection.CALL, is_pre_breakout_ok=True, breakout_confirmed=True,
        breakout_level=LEVEL, close=LEVEL, entry_zone=ZONE, previous_status=SignalStatus.ENTRY,
    )
    assert status == SignalStatus.ENTRY


def test_invalidated_when_pre_breakout_conditions_stop_holding_after_ready():
    status = determine_status(
        direction=SignalDirection.CALL, is_pre_breakout_ok=False, breakout_confirmed=False,
        breakout_level=LEVEL, close=Decimal("100.50"), entry_zone=ZONE, previous_status=SignalStatus.READY,
    )
    assert status == SignalStatus.INVALIDATED


def test_invalidated_is_terminal_never_reverts_to_entry():
    status = determine_status(
        direction=SignalDirection.CALL, is_pre_breakout_ok=True, breakout_confirmed=True,
        breakout_level=LEVEL, close=Decimal("100.50"), entry_zone=ZONE,
        previous_status=SignalStatus.INVALIDATED,
    )
    assert status == SignalStatus.INVALIDATED


def test_put_direction_invalidated_when_close_rises_back_above_level():
    put_zone = compute_entry_zone(level=LEVEL, atr14=Decimal(2), direction=SignalDirection.PUT)
    status = determine_status(
        direction=SignalDirection.PUT, is_pre_breakout_ok=True, breakout_confirmed=False,
        breakout_level=LEVEL, close=Decimal("100.50"), entry_zone=put_zone, previous_status=SignalStatus.ENTRY,
    )
    assert status == SignalStatus.INVALIDATED
