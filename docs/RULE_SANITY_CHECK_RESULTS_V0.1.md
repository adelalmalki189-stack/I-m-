# Rule Sanity Check — Results (V0.1, first run)

Reference run for docs/BACKTEST_PLAN_V0.1.md §6 ("قبل الانتقال لـPaper Trading... تسجيل نسخة strategy_parameters المستخدمة في هذا الفحص كنقطة مرجعية أولى"). This is the first, and so far only, Rule Sanity Check run against the V0.1 rule set.

- **Runner**: `app/scripts/run_rule_sanity_check.py` (`run_rule_sanity_check()` / `python -m app.scripts.run_rule_sanity_check`)
- **calc_version reference point**: `v0.1.0-rule-sanity-check`
- **Sample**: 15 synthetic symbols (`SYN01`-`SYN15`, one fixed seed per symbol — see `DEFAULT_SYMBOLS_AND_SEEDS`), 130 synthetic trading days each (20-day RVOL baseline warm-up + 110 tradeable days ≈ 22 weeks), both CALL and PUT evaluated independently per symbol (30 symbol/direction combinations total).
- **Market context**: fixed `OverallMarketRegime.SUPPORTIVE` for the primary run (see "Market context sensitivity" below for NEUTRAL/RESTRICTIVE).
- **Data**: `app/replay/synthetic_market.py` — a deterministic seeded random walk with occasional injected "trend days", **not real historical data** (none is wired into this sandbox). Quantitative figures below (mean scores, correlations) are therefore indicative of the rule *logic's* behavior, not a real-world win-rate estimate — exactly the limitation docs/BACKTEST_PLAN_V0.1.md §3 anticipates and permits.

## Headline numbers (SUPPORTIVE regime)

- **915 total ENTRY signals** across 30 symbol/direction combinations over ~22 tradeable weeks each.
- That is roughly **1.4 signals per symbol/direction per week** — squarely inside §5's own stated expectation ("بضع إشارات أسبوعياً لكل سهم"), not the "every few minutes" disaster case §5 warns about.
- **No symbol/direction combination was flagged `OVER-TRIGGERING`** (threshold: >5 signals/week — set well above the expected healthy range so only genuine excess trips it).
- **Final Hard Gate pass rate was 96-100% per combination** for signals that reached ENTRY. This is expected, not a red flag: `compute_target` always sizes the underlying target at exactly 2R, so the R:R gate (>= 2R) passes by construction whenever `recommended_quantity >= 1`; the rare misses are `recommended_quantity == 0` cases (account/risk sizing rejected the trade), which is the gate working as intended, not a bypass.
- **Final scores clustered tightly in the low-to-mid 90s** (means per combination ranged ~89-94/100) with no combination showing a materially different distribution.
- **Score-vs-forward-return correlation was inconsistent** — some symbol/direction combinations showed a positive correlation between `final_score` and the 10-bar forward-return proxy, others near-zero or slightly negative, no combination showed a strong, clearly negative correlation.

## Interpretation

1. **No disaster found.** Signal frequency is reasonable, no runaway triggering, and the Final Hard Gate's individual checks (market/setup/timing/liquidity/risk/R:R/event-risk/data-quality) behave exactly as specified whenever exercised (see the `RESTRICTIVE`-regime finding below).
2. **The tight score clustering is an artifact of this harness's own documented simplifications, not a Scoring Engine defect.** Two of the seven sub-scores are held constant by design in `app/replay/harness.py` (`market_context_score` — one fixed regime for the whole run; `option_quality_score` — one fixed synthetic contract per signal), and `risk_reward_score` is nearly constant because `compute_target` always targets exactly 2R. With 3 of 7 weighted sub-scores barely varying, a narrow overall distribution is the expected outcome of *this specific harness*, not evidence that the underlying formulas can't discriminate — Phase 4's golden scenario and the per-engine unit test matrices already show each sub-score responding correctly to its own inputs in isolation.
3. **The inconsistent score/return correlation is a direct consequence of (2):** with final_score compressed into a ~5-point band, a 10-bar forward-return proxy computed from a pure random walk has little room to show a clean relationship either way. This is a genuine limitation of the lightweight harness, not a finding that the Scoring Engine's weighting is wrong — a real signal would need either genuinely varying market context/contract quality inputs, or real (not random-walk) price data, before a score/outcome correlation from this kind of check would be informative. Recorded here rather than hidden, per the plan's own diagnostic (not optimization) framing.
4. **Market context sensitivity was verified directly, not just observed.** Re-running with `OverallMarketRegime.NEUTRAL` and `OverallMarketRegime.RESTRICTIVE` produced **zero signals in both cases**, across all 15 symbols. This is exactly the specified behavior — `evaluate_setup_conditions`'s `market_context_supportive` (and therefore `pre_breakout_ok`) requires `SUPPORTIVE`; the Timing Engine never advances past `WAIT` without it — and is now pinned down as a permanent regression test (`tests/test_rule_sanity_check.py::test_restrictive_regime_yields_zero_signals_for_every_symbol`), not just a one-off observation from this run.

## Conclusion against docs/BACKTEST_PLAN_V0.1.md §6

1. No logical disaster (§5's own checklist) was found in this run.
2. Signal rate and general behavior are reasonable.
3. This document records `v0.1.0-rule-sanity-check` as the first reference point for comparing against real Paper Trading results later.

This check does **not** replace Paper Trading (per §6's own closing line) — it only clears the path from "a written idea" to a monitored paper account, which is the next step once real IBKR data and a live scanner loop exist.
