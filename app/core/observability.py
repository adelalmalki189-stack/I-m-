"""Logging, request IDs, security headers, error tracking and health checks."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.database import get_session

log = logging.getLogger("bud.http")
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Pages use inline <script>/<style> and Google Fonts; everything else stays same-origin.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def init_error_tracking() -> None:
    """Send unhandled errors to Sentry when SENTRY_DSN is set (no-op otherwise)."""
    if not settings.sentry_dsn:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.0,
        send_default_pii=False,  # never ship customer data (names, phones, bodies) to the tracker
    )


def install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            log.exception("unhandled error id=%s %s %s", request_id, request.method, request.url.path)
            if settings.sentry_dsn:
                import sentry_sdk

                sentry_sdk.capture_exception(exc)
            response = JSONResponse(
                status_code=500,
                content={
                    "detail": {
                        "error_code": "INTERNAL_ERROR",
                        "message": "Unexpected error",
                        "request_id": request_id,
                    }
                },
            )
        elapsed = (time.perf_counter() - started) * 1000
        if not request.url.path.startswith(("/dashboard/", "/health")):
            log.info(
                "%s %s %s %.0fms id=%s",
                request.method,
                request.url.path,
                response.status_code,
                elapsed,
                request_id,
            )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith(("/insurance", "/auth")):
            response.headers.setdefault("Cache-Control", "no-store")  # personal data: never cache
        return response


health = APIRouter(tags=["health"])


@health.get("/health", include_in_schema=False)
async def liveness() -> dict:
    """The process is up (used by Docker/orchestrator restarts)."""
    return {"status": "ok"}


def _migration_head() -> str | None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


@health.get("/health/ready", include_in_schema=False)
async def readiness() -> JSONResponse:
    """Database reachable and migrated to this release's schema (used by deploys and load balancers)."""
    checks: dict[str, str] = {}
    try:
        async with get_session() as session:
            await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
            try:
                current = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            except Exception:
                current = None
            head = _migration_head()
            checks["migrations"] = "ok" if current == head else f"at {current}, expected {head}"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503, content={"status": "ok" if healthy else "degraded", **checks}
    )
