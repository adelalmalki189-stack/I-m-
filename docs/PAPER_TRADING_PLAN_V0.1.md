# Paper Trading Plan — V0.1 (Phase 8)

Per `docs/IMPLEMENTATION_PLAN_V0.1.md`'s own Phase 8 entry: run live against an IBKR **Paper** account only, for 4-6 weeks of real trading activity, before any discussion of Live. Daily monitoring of `/system/status` and the `outcomes` log; a sharp deviation from the Phase 7 Rule Sanity Check's expectations halts any move toward Live and sends the Calculation Spec back for review.

**What this document is not**: it is not a claim that Paper Trading has been run. No real IBKR connection exists in the environment this codebase was built in — actually running this phase requires the user's own IBKR paper account and a place to run the process continuously (see `docs/DEPLOYMENT_V0.1.md`). This document is the runbook for doing that, plus the tooling (`app/scripts/run_paper_trading_report.py`) to evaluate the results once real data exists.

## 1. Prerequisites

- An IBKR account with **Paper Trading** enabled (no real money is ever at risk in this phase — verify the TWS/Gateway login banner says "Paper Trading" before proceeding).
- TWS or IB Gateway running and logged into the paper account, API access enabled (Configure → API → Settings → "Enable ActiveX and Socket Clients"), listening on the paper port (7497 for TWS, 4002 for IB Gateway).
- PostgreSQL and Redis reachable, migrated (`alembic upgrade head`) — see `docs/DEPLOYMENT_V0.1.md`.
- `trading_calendar` populated for the run's date range: `python -m app.scripts.populate_trading_calendar --start <date> --end <date>`.
- At least one `instruments` row per symbol you intend to track, `is_active=true` (the Scanner Loop creates these automatically on startup for whatever `--symbol` flags you pass).
- A `portfolio_risk_config` row reflecting your actual paper account size and risk tolerance (`PUT /risk/config`, or let it default — see `app/models/risk.py`).

## 2. Starting the run

```bash
export DATABASE_URL=postgresql+asyncpg://...
export REDIS_URL=redis://...
export IBKR_HOST=...        # wherever TWS/IB Gateway is reachable from this process
export IBKR_PORT=7497       # 7497 = TWS paper, 4002 = IB Gateway paper
export ENVIRONMENT=production   # enables the DataProvider allow-list guard (docs/TESTING_SPEC_V0.1.md §2)

alembic upgrade head
python -m app.scripts.run_live_scanner --symbol NVDA --symbol AMD --symbol SPY_extra_if_wanted
```

Separately, run the FastAPI app (dashboard + API) so you can actually watch it:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Both processes share the same `DATABASE_URL`/`REDIS_URL` — the dashboard reads what the scanner loop writes. The dashboard is served at `/dashboard/`.

Outcome tracking is not automatic yet in-process — schedule it (cron, systemd timer, or a simple `while true; do ...; sleep 300; done` loop) alongside the scanner loop:

```bash
*/5 * * * * cd /path/to/repo && python -m app.scripts.run_outcome_tracker
```

## 3. Daily monitoring checklist

Per the Implementation Plan's own instruction — do this **every trading day** the run is active, not just at the end:

- [ ] `GET /system/status` — `degraded_mode` should be `false` during market hours. Any unexplained `true` means the data feed dropped; check IBKR connectivity before trusting that day's signals.
- [ ] Dashboard shows the expected number of new signals for the day — not zero across every tracked symbol (possible data/config problem) and not dozens per symbol (possible broken condition — the exact "disaster check" pattern from `docs/RULE_SANITY_CHECK_RESULTS_V0.1.md`).
- [ ] Spot-check one signal's `/signals/{id}/why` against the raw chart — does the frozen level, invalidation, and target make visual sense?
- [ ] If you manually entered any trade the dashboard flagged, log it via `POST /trades` (the Trade Journal) the same day — the Paper Trading Report below is only as good as this log.

## 4. Weekly review — the Paper Trading Report

Once at least a few days of data exist:

```bash
python -m app.scripts.run_paper_trading_report --days 7
```

This aggregates real `signals` / `outcomes` / `trades` rows (not a Replay) into the same diagnostic shape as the Phase 7 baseline:

- signal frequency (per week),
- mean Final Opportunity Score and Final Hard Gate pass rate,
- mean MFE at the last outcome checkpoint (60 min) as a % of entry price, and its correlation with `final_score`,
- **actual realized P&L and win rate** from the manually-logged Trade Journal — the one number no synthetic check can provide.

Compare every number against `docs/RULE_SANITY_CHECK_RESULTS_V0.1.md`. That document is explicitly recorded as "the first reference point for comparing against real Paper Trading results later" (`docs/BACKTEST_PLAN_V0.1.md` §6) — this is that comparison.

## 5. What counts as a deviation worth stopping for

Per the Implementation Plan's own instruction ("انحراف حاد... يُوقف الإضافة لـLive ويُعاد فحص Calculation Spec"), stop and re-review — do not just keep collecting data hoping it improves — if any of these hold over a sustained period (a single bad day is noise; a full week is a pattern):

- Signal frequency is wildly outside the Phase 7 range (near-zero every day, or far more than the "a few per symbol per week" the Rule Sanity Check found reasonable).
- `final_score` shows **no** positive relationship with actual outcomes (`score_outcome_correlation` consistently near zero or negative across multiple weekly reports) — the scoring formula itself (Methodology §22, this codebase's own V0.1 sub-score formulas in `app/engines/scoring_engine.py`) may need rework, not just parameter tuning.
- The Final Hard Gate passes at a rate wildly different from Phase 7's near-100% (mostly explained there by every entry sizing to exactly 2R) — a much lower pass rate live could mean liquidity/spread assumptions don't hold on real option chains.
- Repeated `degraded_mode` incidents during market hours (a real infrastructure problem, not a strategy problem, but still blocking).
- The Trade Journal's realized win rate/P&L is sharply worse than what the MFE/MAE outcome data would suggest — a sign of execution slippage or a gap between "what the model saw" and "what you were actually able to fill" that Live trading would only make worse.

## 6. Go/no-go into Live

Only after 4-6 weeks of paper data with no unresolved deviation from §5, and only as a decision the user makes deliberately — this codebase does not (and should not) automate that transition. When ready, the next engineering work is wiring an execution path from "manual Trade Journal entry" toward whatever the user decides for Live — out of scope for V0.1, which per the original spec is explicitly manual-execution, decision-support only.
