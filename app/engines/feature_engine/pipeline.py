"""Feature Engine orchestrator — combines vwap/ema/atr/rvol/structure into
one row per candle, shaped to match docs/DATA_SPEC_V0.1.md §4 technical_features.

Deliberately still a pure function of its inputs: no DB session, no
DataProvider. The caller (a live ingestion loop in a later phase, or the
Replay harness in docs/BACKTEST_PLAN_V0.1.md) is responsible for:
  - selecting the correct `baseline_sessions_for_rvol` (already filtered to
    exclude half-days and high-severity event days — §3.2), and
  - persisting the returned rows with a `calc_version`.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.data_providers.schemas import Candle
from app.engines.feature_engine.atr import DEFAULT_ATR_PERIOD, compute_atr
from app.engines.feature_engine.ema import compute_ema, compute_slope_series
from app.engines.feature_engine.rvol import DEFAULT_MIN_VALID_DAYS, compute_rvol_for_session
from app.engines.feature_engine.session_levels import previous_day_high_low, running_day_high_low
from app.engines.feature_engine.structure import DEFAULT_SWING_K, build_swing_snapshots, find_confirmed_swings
from app.engines.feature_engine.vwap import compute_session_vwap
from app.models.enums import MarketStructureState, TrendSlope

DEFAULT_EMA_PERIODS = (20, 50)


@dataclass(frozen=True, slots=True)
class FeatureRow:
    symbol: str
    timestamp: datetime
    vwap: Decimal
    ema20: Decimal | None
    ema50: Decimal | None
    ema20_slope: TrendSlope | None
    rvol: Decimal | None
    atr14: Decimal | None
    pdh: Decimal | None
    pdl: Decimal | None
    day_high: Decimal
    day_low: Decimal
    swing_high: Decimal | None
    swing_low: Decimal | None
    swing_confirmed_at: datetime | None
    market_structure: MarketStructureState | None
    calc_version: str


def compute_technical_features_for_session(
    current_session_candles: Sequence[Candle],
    *,
    previous_session_candles: Sequence[Candle] | None,
    baseline_sessions_for_rvol: Sequence[Sequence[Candle]],
    calc_version: str,
    ema_periods: tuple[int, int] = DEFAULT_EMA_PERIODS,
    atr_period: int = DEFAULT_ATR_PERIOD,
    swing_k: int = DEFAULT_SWING_K,
    rvol_min_valid_days: int = DEFAULT_MIN_VALID_DAYS,
    prior_candles_for_continuity: Sequence[Candle] = (),
) -> list[FeatureRow]:
    """`prior_candles_for_continuity` (default: none) lets EMA20/EMA50 and
    ATR14 warm up from real prior history instead of reseeding every
    session. docs/CALCULATION_SPEC_V0.1.md §2 explicitly requires EMA to
    be "مستمرة عبر الأيام (لا تُصفَّر يومياً)" — seeded only once, ever, per
    symbol — but every caller through Phase 6 only ever passed one
    session, so this parameter defaulted away and nothing exercised it
    until Phase 7's multi-day Replay harness did. Left opt-in (default
    `()`, reproducing the exact prior single-session behavior bit-for-bit)
    rather than changed by default, because flipping the default would
    silently change every frozen golden-fixture value computed through
    Phase 6 — a deliberate, separately-reviewed migration, not a side
    effect of adding Phase 7. The Replay harness (app/replay/harness.py)
    opts in; production ingestion should too once it exists.
    """
    if not current_session_candles:
        return []

    prior_closes = [c.close for c in prior_candles_for_continuity]
    prior_count = len(prior_closes)

    closes = [c.close for c in current_session_candles]
    vwap_series = compute_session_vwap(current_session_candles)

    # Slope is computed on the full (prior + current) EMA series before
    # slicing, so a continuation day's first few candles still see the
    # real trend from the end of the prior session instead of a false gap.
    ema_fast_full = compute_ema(prior_closes + closes, ema_periods[0])
    ema_slow_full = compute_ema(prior_closes + closes, ema_periods[1])
    slope_full = compute_slope_series(ema_fast_full)
    ema_fast_series = ema_fast_full[prior_count:]
    ema_slow_series = ema_slow_full[prior_count:]
    slope_series = slope_full[prior_count:]

    atr_series = compute_atr([*prior_candles_for_continuity, *current_session_candles], atr_period)[prior_count:]
    rvol_series = compute_rvol_for_session(
        current_session_candles, baseline_sessions_for_rvol, min_valid_days=rvol_min_valid_days
    )
    swings = find_confirmed_swings(current_session_candles, k=swing_k)
    snapshots = build_swing_snapshots(current_session_candles, swings)
    day_high_low_series = running_day_high_low(current_session_candles)

    pdh: Decimal | None = None
    pdl: Decimal | None = None
    if previous_session_candles:
        pdh, pdl = previous_day_high_low(previous_session_candles)

    rows: list[FeatureRow] = []
    for i, candle in enumerate(current_session_candles):
        snapshot = snapshots[i]
        swing_confirmed_at = (
            current_session_candles[snapshot.swing_confirmed_at_index].timestamp
            if snapshot.swing_confirmed_at_index is not None
            else None
        )
        day_high, day_low = day_high_low_series[i]

        rows.append(
            FeatureRow(
                symbol=candle.symbol,
                timestamp=candle.timestamp,
                vwap=vwap_series[i],
                ema20=ema_fast_series[i],
                ema50=ema_slow_series[i],
                ema20_slope=slope_series[i],
                rvol=rvol_series[i],
                atr14=atr_series[i],
                pdh=pdh,
                pdl=pdl,
                day_high=day_high,
                day_low=day_low,
                swing_high=snapshot.swing_high,
                swing_low=snapshot.swing_low,
                swing_confirmed_at=swing_confirmed_at,
                market_structure=snapshot.market_structure,
                calc_version=calc_version,
            )
        )

    return rows
