"""Replay Mode (docs/BACKTEST_PLAN_V0.1.md) — runs the exact same
production engines against historical-shaped data instead of a live feed.

No real market data source is wired into this sandbox (that lands with a
live IBKR connection in a running deployment), so app/replay/synthetic_market.py
generates a deterministic stand-in — the same honest limitation already
documented on every golden fixture since Phase 1
(tests/fixtures/golden/*.py): not real market data, but internally
consistent and reproducible enough to catch a broken formula or a runaway
signal-frequency bug before real capital is ever involved.
"""
