"""FastAPI entrypoint — Modular Monolith (Methodology Part 2 §18).

Routers are added module-by-module per phase (scanner, risk, trades,
events, strategy-parameters, ws) per docs/API_SPEC_V0.1.md without
splitting this into separate services — that's an explicit V0.1 decision,
not an oversight.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import ws
from app.api.routes import events, market, risk, scanner, signals, strategy_parameters, system, trades

app = FastAPI(
    title="Options Trading Decision Support System",
    version="0.1.0",
)

app.include_router(system.router)
app.include_router(signals.router)
app.include_router(market.router)
app.include_router(scanner.router)
app.include_router(trades.router)
app.include_router(risk.router)
app.include_router(events.router)
app.include_router(strategy_parameters.router)
app.include_router(ws.router)

DASHBOARD_DIR = Path(__file__).parent / "static" / "dashboard"
app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard/")
