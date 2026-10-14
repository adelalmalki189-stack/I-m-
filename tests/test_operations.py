"""Phase 0/5: health checks, security headers, demo data."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.demo import ProductionRefused, seed_demo
from app.insurance.models import Customer, InsuranceClaim, InsurancePolicy
from app.main import app


def test_liveness_and_security_headers():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.json() == {"status": "ok"}
        page = client.get("/dashboard/login.html")
        assert page.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert page.headers["x-request-id"]
        api = client.get("/insurance/catalog", headers={"X-Request-ID": "abc123"})
        assert api.headers["x-request-id"] == "abc123" and api.headers["cache-control"] == "no-store"


async def test_readiness_reports_unmigrated_database(db_session):
    # The test database is built with create_all, so it has no alembic_version row.
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["database"] == "ok" and response.json()["migrations"].startswith("at None")


async def test_seed_demo_is_idempotent_and_refuses_production(db_session):
    with pytest.raises(ProductionRefused):
        await seed_demo(db_session, "production")
    counts = await seed_demo(db_session, "development")
    assert counts == {"customers": 5, "policies": 2, "claims": 1}
    assert await seed_demo(db_session, "development") == {"created": 0}
    assert await db_session.scalar(select(func.count()).select_from(Customer)) == 5
    assert await db_session.scalar(select(func.count()).select_from(InsurancePolicy)) == 2
    assert await db_session.scalar(select(func.count()).select_from(InsuranceClaim)) == 1
    with TestClient(app) as client:
        report = client.get("/insurance/reports/summary").json()
        assert report["policies_issued"] == 2 and report["renewals"]["due_30"] == 1
