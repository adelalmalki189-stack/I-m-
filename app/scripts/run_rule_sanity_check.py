"""Rule Sanity Check — docs/BACKTEST_PLAN_V0.1.md's lightweight diagnostic
runner. Deliberately NOT a parameter-optimization backtest (explicitly out
of scope per the plan's own §1: no grid search, no walk-forward
optimization) — this only checks for "completely random output" or an
"obvious disaster" (§5) before Paper Trading, using the exact same
production engines as everything else in this codebase (§2), via
app/replay/harness.py.

No real historical data source exists in this sandbox, so the sample here
is app/replay/synthetic_market.py's deterministic synthetic generator —
the same honest limitation flagged on every golden fixture since Phase 1.
The quantitative numbers below (win-rate-like figures, exact correlations)
are therefore not meaningful in themselves; what matters is the shape of
the diagnostics the plan actually asks for: signal frequency, whether
score correlates at all with a forward-return proxy, and whether any
symbol/direction combination is flagged as a rule-breakage disaster.

Run: python -m app.scripts.run_rule_sanity_check
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from decimal import Decimal

from app.models.enums import OverallMarketRegime, SignalDirection
from app.replay.harness import ReplaySignalResult, replay_symbol
from app.replay.synthetic_market import generate_symbol_history

DEFAULT_NUM_DAYS = 130  # ~6 months of trading days -- docs/BACKTEST_PLAN_V0.1.md §3
DEFAULT_MIN_BASELINE_DAYS = 20
TRADING_DAYS_PER_WEEK = 5
# §5's own disaster example is "breakout fires every few minutes"; translated to a
# per-week rate this is deliberately generous (a few times a week is expected/healthy
# per §5 -- "بضع إشارات أسبوعياً"), so only genuinely excessive frequency trips it.
OVER_TRIGGER_SIGNALS_PER_WEEK = 5.0
# 15 symbols per §3's "15-20 عالي السيولة" sample size; seeds are arbitrary but fixed
# so the report is reproducible run to run.
DEFAULT_SYMBOLS_AND_SEEDS = [(f"SYN{i:02d}", i) for i in range(1, 16)]


@dataclass(frozen=True, slots=True)
class SymbolDiagnostics:
    symbol: str
    direction: SignalDirection
    signal_count: int
    signals_per_week: float
    final_gate_pass_rate: float
    mean_final_score: Decimal | None
    mean_forward_return: Decimal | None
    score_return_correlation: float | None
    over_triggering: bool


def _correlation(scores: list[float], returns: list[float]) -> float | None:
    """None (not 0) when there isn't enough variation to compute a
    meaningful correlation -- e.g. a symbol/direction with 0 or 1 signals."""
    if len(scores) < 2 or len(set(scores)) < 2 or len(set(returns)) < 2:
        return None
    return statistics.correlation(scores, returns)


def _diagnose(
    symbol: str, direction: SignalDirection, results: list[ReplaySignalResult], tradeable_days: int
) -> SymbolDiagnostics:
    weeks = max(tradeable_days / TRADING_DAYS_PER_WEEK, 1e-9)
    signals_per_week = len(results) / weeks

    scored_pairs = [
        (float(r.final_score), float(r.forward_return_10bars))
        for r in results
        if r.forward_return_10bars is not None
    ]

    mean_final_score = Decimal(str(statistics.mean(float(r.final_score) for r in results))) if results else None
    mean_forward_return = (
        Decimal(str(statistics.mean(ret for _, ret in scored_pairs))) if scored_pairs else None
    )
    correlation = _correlation([s for s, _ in scored_pairs], [r for _, r in scored_pairs])
    pass_rate = (sum(1 for r in results if r.passes_final_gate) / len(results)) if results else 0.0

    return SymbolDiagnostics(
        symbol=symbol,
        direction=direction,
        signal_count=len(results),
        signals_per_week=signals_per_week,
        final_gate_pass_rate=pass_rate,
        mean_final_score=mean_final_score,
        mean_forward_return=mean_forward_return,
        score_return_correlation=correlation,
        over_triggering=signals_per_week > OVER_TRIGGER_SIGNALS_PER_WEEK,
    )


def run_rule_sanity_check(
    *,
    symbols_and_seeds: list[tuple[str, int]] = DEFAULT_SYMBOLS_AND_SEEDS,
    num_days: int = DEFAULT_NUM_DAYS,
    min_baseline_days: int = DEFAULT_MIN_BASELINE_DAYS,
    overall_market_regime: OverallMarketRegime = OverallMarketRegime.SUPPORTIVE,
    calc_version: str = "v0.1.0-rule-sanity-check",
) -> list[SymbolDiagnostics]:
    """One CALL run and one PUT run per (symbol, seed) -- §3/§4's replay
    loop, aggregated into the §5 diagnostics rather than raw signals."""
    diagnostics: list[SymbolDiagnostics] = []
    tradeable_days = max(num_days - min_baseline_days, 0)

    for symbol, seed in symbols_and_seeds:
        sessions = generate_symbol_history(symbol=symbol, num_days=num_days, seed=seed)
        for direction in (SignalDirection.CALL, SignalDirection.PUT):
            results = replay_symbol(
                symbol=symbol,
                daily_sessions=sessions,
                calc_version=calc_version,
                overall_market_regime=overall_market_regime,
                direction=direction,
                min_baseline_days=min_baseline_days,
            )
            diagnostics.append(_diagnose(symbol, direction, results, tradeable_days))

    return diagnostics


def format_report(diagnostics: list[SymbolDiagnostics]) -> str:
    header = (
        f"{'symbol':<8}{'dir':<6}{'signals':<9}{'sig/week':<10}"
        f"{'gate pass%':<12}{'mean score':<12}{'mean fwd ret':<14}{'corr':<8}{'flag'}"
    )
    lines = [
        "Rule Sanity Check report (docs/BACKTEST_PLAN_V0.1.md) -- diagnostic only, not an optimization result.",
        "",
        header,
    ]

    total_signals = 0
    any_over_triggering = False
    for d in diagnostics:
        total_signals += d.signal_count
        any_over_triggering = any_over_triggering or d.over_triggering
        flag = "OVER-TRIGGERING" if d.over_triggering else ""
        score_str = str(round(d.mean_final_score, 1)) if d.mean_final_score is not None else "-"
        ret_str = str(round(d.mean_forward_return, 4)) if d.mean_forward_return is not None else "-"
        corr_str = f"{d.score_return_correlation:.2f}" if d.score_return_correlation is not None else "-"
        lines.append(
            f"{d.symbol:<8}{d.direction.value:<6}{d.signal_count:<9}{d.signals_per_week:<10.2f}"
            f"{d.final_gate_pass_rate * 100:<12.1f}{score_str:<12}{ret_str:<14}{corr_str:<8}{flag}"
        )

    lines.append("")
    lines.append(f"Total signals across all symbols/directions: {total_signals}")
    lines.append(
        "DISASTER CHECK (frequency): "
        + ("over-triggering detected -- see flagged rows above" if any_over_triggering else "none detected")
    )
    lines.append(
        "DISASTER CHECK (silence): "
        + (
            "zero signals across every symbol/direction -- conditions may be broken or too strict"
            if total_signals == 0
            else "at least one signal generated"
        )
    )
    return "\n".join(lines)


def main() -> None:
    diagnostics = run_rule_sanity_check()
    print(format_report(diagnostics))


if __name__ == "__main__":
    main()
