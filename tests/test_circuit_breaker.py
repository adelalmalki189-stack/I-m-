"""Unit tests for the Circuit Breaker pure function
(docs/OPERATIONAL_SPEC_V0.1.md §3, app/core/circuit_breaker.py).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.circuit_breaker import DEFAULT_OUTAGE_THRESHOLD_SECONDS, is_degraded

NOW = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)


def test_no_update_ever_recorded_is_degraded():
    assert is_degraded(last_successful_update_at=None, now=NOW) is True


def test_recent_update_is_not_degraded():
    last_update = NOW - timedelta(seconds=5)
    assert is_degraded(last_successful_update_at=last_update, now=NOW) is False


def test_update_exactly_at_threshold_is_not_degraded():
    last_update = NOW - timedelta(seconds=DEFAULT_OUTAGE_THRESHOLD_SECONDS)
    assert is_degraded(last_successful_update_at=last_update, now=NOW) is False


def test_update_one_second_past_threshold_is_degraded():
    last_update = NOW - timedelta(seconds=DEFAULT_OUTAGE_THRESHOLD_SECONDS + 1)
    assert is_degraded(last_successful_update_at=last_update, now=NOW) is True


def test_custom_threshold_is_respected():
    last_update = NOW - timedelta(seconds=20)
    assert is_degraded(last_successful_update_at=last_update, now=NOW, outage_threshold_seconds=10) is True
    assert is_degraded(last_successful_update_at=last_update, now=NOW, outage_threshold_seconds=30) is False
