"""Tests for the Rule Sanity Check aggregation script
(app/scripts/run_rule_sanity_check.py) — docs/BACKTEST_PLAN_V0.1.md's
lightweight diagnostic runner. These check the aggregation math and report
formatting only; the harness itself is already covered by tests/replay/.
"""
from __future__ import annotations

from decimal import Decimal

from app.models.enums import OverallMarketRegime, SignalDirection
from app.replay.harness import ReplaySignalResult
from app.scripts.run_rule_sanity_check import (
    OVER_TRIGGER_SIGNALS_PER_WEEK,
    SymbolDiagnostics,
    _correlation,
    _diagnose,
    format_report,
    run_rule_sanity_check,
)

SMALL_SYMBOLS_AND_SEEDS = [("SYN01", 1), ("SYN02", 2), ("SYN03", 3)]


def _fake_result(*, score: float, forward_return: float | None, passes: bool = True) -> ReplaySignalResult:
    return ReplaySignalResult(
        symbol="X", direction=SignalDirection.CALL, day_index=25, candle_index=10,
        entry_price=Decimal(100), invalidation=Decimal(95), target=Decimal(110),
        final_score=Decimal(str(score)),
        forward_return_10bars=Decimal(str(forward_return)) if forward_return is not None else None,
        passes_final_gate=passes, recommended_quantity=1 if passes else 0, r_multiple=Decimal(2),
    )


def test_run_rule_sanity_check_produces_one_diagnostic_per_symbol_and_direction():
    diagnostics = run_rule_sanity_check(
        symbols_and_seeds=SMALL_SYMBOLS_AND_SEEDS, num_days=60, min_baseline_days=20
    )
    assert len(diagnostics) == len(SMALL_SYMBOLS_AND_SEEDS) * 2
    seen = {(d.symbol, d.direction) for d in diagnostics}
    assert seen == {
        (symbol, direction) for symbol, _ in SMALL_SYMBOLS_AND_SEEDS for direction in SignalDirection
    }


def test_restrictive_regime_yields_zero_signals_for_every_symbol():
    diagnostics = run_rule_sanity_check(
        symbols_and_seeds=SMALL_SYMBOLS_AND_SEEDS, num_days=60, min_baseline_days=20,
        overall_market_regime=OverallMarketRegime.RESTRICTIVE,
    )
    assert all(d.signal_count == 0 for d in diagnostics)
    assert all(d.mean_final_score is None for d in diagnostics)
    assert all(d.score_return_correlation is None for d in diagnostics)


def test_diagnose_computes_signals_per_week_and_pass_rate():
    results = [_fake_result(score=90, forward_return=0.01, passes=True) for _ in range(10)]
    diag = _diagnose("X", SignalDirection.CALL, results, tradeable_days=10)  # exactly 2 weeks

    assert diag.signal_count == 10
    assert diag.signals_per_week == 5.0
    assert diag.final_gate_pass_rate == 1.0
    assert diag.over_triggering is False  # exactly at the threshold, not over it


def test_diagnose_flags_over_triggering_above_threshold():
    excessive = int(OVER_TRIGGER_SIGNALS_PER_WEEK * 2) + 10
    results = [_fake_result(score=90, forward_return=0.01) for _ in range(excessive)]
    diag = _diagnose("X", SignalDirection.CALL, results, tradeable_days=5)  # 1 week

    assert diag.over_triggering is True


def test_diagnose_handles_zero_signals_without_crashing():
    diag = _diagnose("X", SignalDirection.CALL, [], tradeable_days=100)

    assert diag.signal_count == 0
    assert diag.mean_final_score is None
    assert diag.mean_forward_return is None
    assert diag.score_return_correlation is None
    assert diag.final_gate_pass_rate == 0.0


def test_correlation_requires_variation_in_both_series():
    assert _correlation([1.0], [1.0]) is None  # too few points
    assert _correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None  # no variation in scores
    assert _correlation([1.0, 2.0, 3.0], [1.0, 1.0, 1.0]) is None  # no variation in returns
    assert _correlation([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0


def test_format_report_includes_every_row_and_both_disaster_checks():
    diagnostics = [
        SymbolDiagnostics(
            symbol="AAA", direction=SignalDirection.CALL, signal_count=3, signals_per_week=1.5,
            final_gate_pass_rate=1.0, mean_final_score=Decimal(90), mean_forward_return=Decimal("0.01"),
            score_return_correlation=0.5, over_triggering=False,
        ),
        SymbolDiagnostics(
            symbol="BBB", direction=SignalDirection.PUT, signal_count=0, signals_per_week=0.0,
            final_gate_pass_rate=0.0, mean_final_score=None, mean_forward_return=None,
            score_return_correlation=None, over_triggering=False,
        ),
    ]
    report = format_report(diagnostics)

    assert "AAA" in report
    assert "BBB" in report
    assert "Total signals across all symbols/directions: 3" in report
    assert "DISASTER CHECK (frequency): none detected" in report
    assert "DISASTER CHECK (silence): at least one signal generated" in report


def test_format_report_flags_over_triggering_and_total_silence():
    over_triggering_diag = SymbolDiagnostics(
        symbol="CCC", direction=SignalDirection.CALL, signal_count=100, signals_per_week=50.0,
        final_gate_pass_rate=1.0, mean_final_score=Decimal(90), mean_forward_return=Decimal("0.01"),
        score_return_correlation=0.1, over_triggering=True,
    )
    report = format_report([over_triggering_diag])
    assert "OVER-TRIGGERING" in report
    assert "DISASTER CHECK (frequency): over-triggering detected" in report

    silent = SymbolDiagnostics(
        symbol="DDD", direction=SignalDirection.PUT, signal_count=0, signals_per_week=0.0,
        final_gate_pass_rate=0.0, mean_final_score=None, mean_forward_return=None,
        score_return_correlation=None, over_triggering=False,
    )
    silent_report = format_report([silent])
    assert "DISASTER CHECK (silence): zero signals" in silent_report
