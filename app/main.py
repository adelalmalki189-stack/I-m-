"""FastAPI entrypoint for BUD (بُعد) — digital insurance brokerage."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import aftersales, auth, automation, insurance

app = FastAPI(
    title="BUD — Digital Insurance Brokerage",
    version="0.1.0",
)

app.include_router(auth.router)
app.include_router(insurance.public_router)
app.include_router(insurance.router)
app.include_router(aftersales.router)
app.include_router(automation.router)

DASHBOARD_DIR = Path(__file__).parent / "static" / "dashboard"
app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard/")
