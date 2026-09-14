"""FastAPI entrypoint — Modular Monolith (Methodology Part 2 §18).

Phase 0 only mounts /system/*. Later phases add routers module-by-module
(scanner, signals, risk, trades, events, strategy-parameters, ws) per
docs/API_SPEC_V0.1.md without splitting this into separate services —
that's an explicit V0.1 decision, not an oversight.
"""
from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import system

app = FastAPI(
    title="Options Trading Decision Support System",
    version="0.1.0",
)

app.include_router(system.router)
