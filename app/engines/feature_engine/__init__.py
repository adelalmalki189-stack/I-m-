"""Feature Engine — pure implementations of docs/CALCULATION_SPEC_V0.1.md §1-5.

Every function here takes plain sequences of closed `Candle` objects (never
a still-forming one — see `Candle.is_complete`) and returns plain values.
No engine talks to a database or a broker directly; that keeps the exact
same code usable from the live pipeline (Phase 5+) and from Replay Mode
(docs/BACKTEST_PLAN_V0.1.md §2), which is the whole point of building this
layer before anything else in Phase 1.
"""
