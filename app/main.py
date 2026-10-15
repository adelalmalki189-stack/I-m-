"""FastAPI entrypoint for BUD (بُعد) — digital insurance brokerage."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import aftersales, auth, automation, insurance, request_review, sheets, tracking
from app.core.config import settings
from app.core.observability import configure_logging, health, init_error_tracking, install_middleware

configure_logging()
init_error_tracking()

# The interactive API docs list every endpoint; keep them off in production.
_docs = settings.environment != "production"
app = FastAPI(
    title="BUD — Digital Insurance Brokerage",
    version="0.1.0",
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)
install_middleware(app)

app.include_router(health)
app.include_router(auth.router)
app.include_router(insurance.public_router)
app.include_router(tracking.router)
app.include_router(request_review.public_router)
app.include_router(insurance.router)
app.include_router(aftersales.router)
app.include_router(automation.router)
app.include_router(request_review.router)
app.include_router(sheets.router)

DASHBOARD_DIR = Path(__file__).parent / "static" / "dashboard"
app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard/")
