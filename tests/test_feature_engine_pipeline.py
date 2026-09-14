"""Phase 1 completion criterion (docs/IMPLEMENTATION_PLAN_V0.1.md):

"technical_features تُملأ صحيحة لعينة تاريخية معروفة يدوياً + Golden
Scenario الأولى تمر."

This file does both: (1) regression-tests the Feature Engine pipeline
against a frozen golden fixture (docs/TESTING_SPEC_V0.1.md §5), and
(2) proves the pipeline's output can actually be persisted through the
technical_features ORM model from docs/DATA_SPEC_V0.1.md.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.engines.feature_engine.pipeline import compute_technical_features_for_session
from app.models.candles import Candle5m
from app.models.enums import TrendSlope
from app.models.instruments import Instrument
from app.models.strategy_parameters import StrategyParameters
from app.models.technical_features import TechnicalFeatures
from tests.fixtures.golden.simple_uptrend_session import (
    build_baseline_sessions,
    build_current_session,
    build_previous_session,
)

GOLDEN_EXPECTED_PATH = Path(__file__).parent / "fixtures" / "golden" / "simple_uptrend_session_expected.json"
CALC_VERSION = "v0.1.0-golden"


def _compute_rows():
    return compute_technical_features_for_session(
        build_current_session(),
        previous_session_candles=build_previous_session(),
        baseline_sessions_for_rvol=build_baseline_sessions(),
        calc_version=CALC_VERSION,
    )


def test_golden_scenario_matches_frozen_expected_output():
    """Any change to a CALCULATION_SPEC_V0.1.md formula that alters this
    output must show up here as a deliberate, reviewed diff to the fixture
    — not as a silent behavior change (docs/TESTING_SPEC_V0.1.md §5)."""
    rows = _compute_rows()
    expected = json.loads(GOLDEN_EXPECTED_PATH.read_text())

    assert len(rows) == len(expected)

    for row, exp in zip(rows, expected, strict=True):
        assert row.symbol == exp["symbol"]
        assert row.timestamp.isoformat() == exp["timestamp"]
        assert row.vwap == Decimal(exp["vwap"])
        assert row.ema20 == (Decimal(exp["ema20"]) if exp["ema20"] is not None else None)
        assert row.ema50 == (Decimal(exp["ema50"]) if exp["ema50"] is not None else None)
        assert row.ema20_slope == (TrendSlope(exp["ema20_slope"]) if exp["ema20_slope"] else None)
        assert row.rvol == (Decimal(exp["rvol"]) if exp["rvol"] is not None else None)
        assert row.atr14 == (Decimal(exp["atr14"]) if exp["atr14"] is not None else None)
        assert row.pdh == Decimal(exp["pdh"])
        assert row.pdl == Decimal(exp["pdl"])
        assert row.day_high == Decimal(exp["day_high"])
        assert row.day_low == Decimal(exp["day_low"])
        assert row.swing_high == (Decimal(exp["swing_high"]) if exp["swing_high"] is not None else None)
        assert row.swing_low == (Decimal(exp["swing_low"]) if exp["swing_low"] is not None else None)
        assert row.market_structure is None if exp["market_structure"] is None else (
            row.market_structure.value == exp["market_structure"]
        )


def test_golden_scenario_rvol_step_change_is_exactly_2_5():
    # Sanity-checkable by hand (docstring in the fixture module): baseline
    # volume is a flat 1000 at every bucket; the second half of the current
    # session trades 2500 -> RVOL must be exactly 2.5, not an approximation.
    rows = _compute_rows()
    assert rows[11].rvol == Decimal(1)
    assert rows[12].rvol == Decimal("2.5")


async def test_technical_features_rows_persist_through_the_orm(db_session):
    rows = _compute_rows()

    db_session.add(Instrument(symbol=rows[0].symbol, exchange="SMART", is_active=True))
    db_session.add(
        StrategyParameters(
            version=CALC_VERSION,
            parameters={"note": "golden fixture pin"},
            effective_from=rows[0].timestamp,
        )
    )
    await db_session.flush()

    for row in rows:
        db_session.add(
            Candle5m(
                symbol=row.symbol,
                timestamp=row.timestamp,
                open=row.vwap,  # fixture doesn't retain raw OHLC here; vwap is a valid stand-in for this check
                high=row.day_high,
                low=row.day_low,
                close=row.vwap,
                volume=1000,
                vwap=row.vwap,
                is_complete=True,
            )
        )
    await db_session.flush()

    for row in rows:
        db_session.add(
            TechnicalFeatures(
                symbol=row.symbol,
                timestamp=row.timestamp,
                ema20=row.ema20,
                ema50=row.ema50,
                ema20_slope=row.ema20_slope,
                rvol=row.rvol,
                atr14=row.atr14,
                pdh=row.pdh,
                pdl=row.pdl,
                day_high=row.day_high,
                day_low=row.day_low,
                swing_high=row.swing_high,
                swing_low=row.swing_low,
                swing_confirmed_at=row.swing_confirmed_at,
                market_structure=row.market_structure,
                calc_version=row.calc_version,
            )
        )
    await db_session.commit()

    result = await db_session.execute(
        select(TechnicalFeatures).where(TechnicalFeatures.symbol == rows[0].symbol)
    )
    persisted = result.scalars().all()
    assert len(persisted) == len(rows)
    assert all(r.calc_version == CALC_VERSION for r in persisted)
