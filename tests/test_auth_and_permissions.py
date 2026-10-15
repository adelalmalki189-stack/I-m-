"""Phase 2: staff login, role permissions, audit trail, and the protected public portal."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import create_manager
from app.core.config import settings
from app.core.security import hash_password
from app.insurance.models import AuditEvent, SalesEmployee
from app.main import app

pytestmark = pytest.mark.real_auth

PASSWORD = "Strong-pass-2026"
PDF = b"%PDF-1.4 test document"
PORTAL_REQUEST = {
    "full_name": "نورة القحطاني",
    "mobile": "0551234567",
    "company_name": "شركة اختبار",
    "cr_number": "1051234567",
    "insurance_type": "travel",
    "product_type": "travel_single",
    "details": {"destination": "لندن", "travelers": 2, "start_date": "2026-12-01", "end_date": "2026-12-10"},
    "consent": True,
}


async def _employee(db_session, email: str, role: str, password: str | None = PASSWORD) -> SalesEmployee:
    employee = SalesEmployee(
        full_name=email.split("@")[0],
        email=email,
        role=role,
        password_hash=hash_password(password) if password else None,
    )
    db_session.add(employee)
    await db_session.commit()
    return employee


def _login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


async def test_staff_api_requires_login_but_portal_is_public(db_session):
    with TestClient(app) as client:
        assert client.get("/insurance/requests").status_code == 401
        assert client.get("/auth/me").status_code == 401
        assert client.get("/insurance/catalog").status_code == 200
        created = client.post("/insurance/portal/requests", json=PORTAL_REQUEST)
        assert created.status_code == 201
        assert created.json()["upload_token"]


async def test_login_me_logout_and_case_insensitive_email(db_session):
    await _employee(db_session, "sara@bud.example", "sales_agent")
    with TestClient(app) as client:
        response = _login(client, "Sara@BUD.example")
        assert response.status_code == 200
        assert response.json()["role_label"] == "مبيعات"
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        assert client.get("/auth/me").json()["email"] == "sara@bud.example"
        assert client.get("/insurance/requests").status_code == 200
        assert client.post("/auth/logout").status_code == 204
        assert client.get("/insurance/requests").status_code == 401


async def test_wrong_password_locks_after_repeated_failures(db_session):
    await _employee(db_session, "omar@bud.example", "manager")
    with TestClient(app) as client:
        for _ in range(5):
            assert _login(client, "omar@bud.example", "wrong-password-1").status_code == 401
        assert _login(client, "omar@bud.example").status_code == 429  # locked even with the right password
        assert _login(client, "nobody@bud.example", "x").status_code == 401  # unknown email looks the same
    failures = (await db_session.execute(select(AuditEvent).where(AuditEvent.action == "login_failed"))).scalars()
    assert len(failures.all()) >= 5


async def test_employee_without_password_cannot_log_in(db_session):
    await _employee(db_session, "new@bud.example", "sales_agent", password=None)
    with TestClient(app) as client:
        assert _login(client, "new@bud.example", "anything-123").status_code == 401


async def test_roles_are_enforced(db_session):
    await _employee(db_session, "sales@bud.example", "sales_agent")
    await _employee(db_session, "uw@bud.example", "underwriter")
    template = {
        "name": "قالب",
        "category": "motor",
        "subject_template": "{request_number}",
        "body_template": "نص الرسالة",
    }
    insurer = {"name": "شركة", "email": "q@insurer.example"}

    with TestClient(app) as sales:
        _login(sales, "sales@bud.example")
        request_id = sales.post("/insurance/portal/requests", json=PORTAL_REQUEST).json()["request_id"]
        assert sales.post("/insurance/templates", json=template).status_code == 403
        assert sales.post("/insurance/insurers", json=insurer).status_code == 403
        assert sales.get("/insurance/audit").status_code == 403
        assert (
            sales.post("/insurance/employees", json={"full_name": "x y", "email": "x@b.example"}).status_code
            == 403
        )
        moved = sales.patch(f"/insurance/requests/{request_id}/status", json={"status": "under_review"})
        assert moved.status_code == 200  # sales can manage the request itself

    with TestClient(app) as uw:
        _login(uw, "uw@bud.example")
        assert uw.post("/insurance/templates", json=template).status_code == 201
        insurer_id = uw.post("/insurance/insurers", json=insurer).json()["insurer_id"]
        drafted = uw.post(f"/insurance/requests/{request_id}/submissions", json={"insurer_ids": [insurer_id]})
        assert drafted.status_code == 201
        assert uw.get("/insurance/audit").status_code == 403


async def test_manager_manages_team_and_reads_audit(db_session):
    await _employee(db_session, "boss@bud.example", "manager")
    with TestClient(app) as boss, TestClient(app) as agent:
        _login(boss, "boss@bud.example")
        weak = boss.post(
            "/insurance/employees", json={"full_name": "خالد", "email": "k@bud.example", "password": "123"}
        )
        assert weak.status_code == 422
        created = boss.post(
            "/insurance/employees",
            json={"full_name": "خالد", "email": "k@bud.example", "role": "sales_agent", "password": PASSWORD},
        )
        assert created.status_code == 201 and created.json()["can_login"] is True
        employee_id = created.json()["employee_id"]

        assert _login(agent, "k@bud.example").status_code == 200
        assert agent.get("/insurance/requests").status_code == 200
        boss.patch(f"/insurance/employees/{employee_id}", json={"is_active": False})
        assert agent.get("/insurance/requests").status_code == 401  # deactivation ends the session

        me = boss.get("/auth/me").json()["employee_id"]
        assert boss.patch(f"/insurance/employees/{me}", json={"role": "sales_agent"}).status_code == 409

        events = boss.get("/insurance/audit").json()
        assert {"employee_created", "employee_updated", "login"} <= {e["action"] for e in events}
        updated = next(e for e in events if e["action"] == "employee_updated")
        assert updated["actor_name"] == "boss" and updated["entity_id"] == employee_id


async def test_change_password_signs_out_other_sessions(db_session):
    await _employee(db_session, "lina@bud.example", "underwriter")
    with TestClient(app) as laptop, TestClient(app) as phone:
        _login(laptop, "lina@bud.example")
        _login(phone, "lina@bud.example")
        body = {"current_password": "wrong", "new_password": "Another-pass-99"}
        assert laptop.post("/auth/change-password", json=body).status_code == 400
        body["current_password"] = PASSWORD
        assert laptop.post("/auth/change-password", json={**body, "new_password": "short"}).status_code == 422
        assert laptop.post("/auth/change-password", json=body).status_code == 204
        assert laptop.get("/auth/me").status_code == 200
        assert phone.get("/auth/me").status_code == 401
        assert _login(phone, "lina@bud.example").status_code == 401
        assert _login(phone, "lina@bud.example", "Another-pass-99").status_code == 200


async def test_portal_uploads_need_the_token_and_real_files(db_session):
    await _employee(db_session, "uw2@bud.example", "underwriter")
    with TestClient(app) as client:
        created = client.post("/insurance/portal/requests", json=PORTAL_REQUEST).json()
        url = f"/insurance/portal/requests/{created['request_id']}/documents"

        def upload(name, data, token):
            return client.post(
                url, files={"file": (name, data, "application/pdf")}, headers={"X-Upload-Token": token}
            )

        assert upload("id.pdf", PDF, "wrong-token").status_code == 403
        assert upload("fake.pdf", b"MZ not really a pdf", created["upload_token"]).status_code == 415
        assert upload("big.pdf", PDF + b"0" * (11 * 1024 * 1024), created["upload_token"]).status_code == 413
        ok = upload("id.pdf", PDF, created["upload_token"])
        assert ok.status_code == 201

        # Staff can fetch it (audited); anonymous users can't.
        assert client.get(f"/insurance/documents/{ok.json()['document_id']}/download").status_code == 401
        _login(client, "uw2@bud.example")
        download = client.get(f"/insurance/documents/{ok.json()['document_id']}/download")
        assert download.status_code == 200 and download.content == PDF
        actions = {e.action for e in (await db_session.execute(select(AuditEvent))).scalars()}
        assert {"portal_request_created", "portal_document_uploaded", "document_downloaded"} <= actions


async def test_portal_is_rate_limited_per_ip(db_session):
    with TestClient(app) as client:
        statuses = [client.post("/insurance/portal/requests", json=PORTAL_REQUEST).status_code for _ in range(11)]
    assert statuses[:10] == [201] * 10 and statuses[10] == 429


async def test_cli_creates_first_manager(db_session):
    assert await create_manager("first@bud.example", "المدير الأول", PASSWORD) == "created"
    assert await create_manager("first@bud.example", "المدير الأول", PASSWORD) == "updated"
    with TestClient(app) as client:
        assert _login(client, "first@bud.example").json()["role"] == "manager"


async def test_dev_login_is_off_by_default(db_session):
    employee = await _employee(db_session, "dev@bud.example", "sales_agent")
    with TestClient(app) as client:
        assert client.get("/auth/dev-login").status_code == 404
        assert client.post("/auth/dev-login", json={"employee_id": employee.employee_id}).status_code == 404


async def test_dev_login_picks_a_staff_member_without_password(db_session, monkeypatch):
    monkeypatch.setattr(settings, "dev_login_enabled", True)
    employee = await _employee(db_session, "claims@bud.example", "claims_officer", password=None)
    with TestClient(app) as client:
        staff = client.get("/auth/dev-login").json()
        assert [s["role_label"] for s in staff] == ["مطالبات"]
        response = client.post("/auth/dev-login", json={"employee_id": employee.employee_id})
        assert response.status_code == 200
        assert client.get("/auth/me").json()["role"] == "claims_officer"
        assert client.post("/auth/dev-login", json={"employee_id": "missing"}).status_code == 404
    actions = (await db_session.execute(select(AuditEvent.action))).scalars().all()
    assert "dev_login" in actions


async def test_dev_login_is_refused_in_production(db_session, monkeypatch):
    monkeypatch.setattr(settings, "dev_login_enabled", True)
    monkeypatch.setattr(settings, "environment", "production")
    employee = await _employee(db_session, "prod@bud.example", "manager")
    with TestClient(app) as client:
        assert client.get("/auth/dev-login").status_code == 404
        assert client.post("/auth/dev-login", json={"employee_id": employee.employee_id}).status_code == 404
