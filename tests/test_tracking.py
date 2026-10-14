"""Customers follow their request with a one-time code sent to their mobile."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.automation.models import CustomerNotification
from app.main import app

pytestmark = pytest.mark.real_auth  # the tracking API must work without any staff session

DETAILS = {
    "vehicle_make": "تويوتا",
    "vehicle_model": "كامري",
    "vehicle_year": 2022,
    "vehicle_count": 1,
    "vehicle_usage": "خاص",
}


def _request(client: TestClient) -> dict:
    return client.post(
        "/insurance/portal/requests",
        json={
            "full_name": "نورة القحطاني",
            "mobile": "0551234567",
            "insurance_type": "motor",
            "product_type": "motor_comprehensive",
            "details": DETAILS,
            "consent": True,
        },
    ).json()


async def test_code_flow_and_status_view(db_session):
    with TestClient(app) as client:
        request = _request(client)
        start = client.post(
            "/insurance/portal/track/start",
            json={"request_number": request["request_number"].lower(), "mobile": "+966 55 123 4567"},
        )
        assert start.status_code == 200 and len(start.json()["dev_code"]) == 6
        code = start.json()["dev_code"]

        body = {"request_number": request["request_number"], "mobile": "0551234567"}
        wrong = "000000" if code != "000000" else "111111"
        assert client.post("/insurance/portal/track/verify", json={**body, "code": wrong}).status_code == 401
        token = client.post("/insurance/portal/track/verify", json={**body, "code": code}).json()["token"]
        # A code works once.
        assert client.post("/insurance/portal/track/verify", json={**body, "code": code}).status_code == 401

        view = client.get("/insurance/portal/track", headers={"X-Track-Token": token}).json()
        assert view["request_number"] == request["request_number"] and view["line"] == "المركبات"
        assert view["steps"][0]["current"] and view["policy"] is None
        assert client.get("/insurance/portal/track", headers={"X-Track-Token": "nope"}).status_code == 401
        assert client.get("/insurance/requests").status_code == 401  # no staff access from tracking

    otp = (
        await db_session.execute(select(CustomerNotification).where(CustomerNotification.kind == "otp"))
    ).scalar_one()
    assert otp.status == "simulated" and code in otp.body  # delivered immediately, not by the worker


async def test_wrong_mobile_gets_same_answer_and_no_code(db_session):
    with TestClient(app) as client:
        request = _request(client)
        response = client.post(
            "/insurance/portal/track/start",
            json={"request_number": request["request_number"], "mobile": "0559999999"},
        )
        assert response.json() == {"sent": True}
        missing = client.post(
            "/insurance/portal/track/start",
            json={"request_number": "IR-00000000-AAAAAAAA", "mobile": "0559999999"},
        )
        assert missing.json() == {"sent": True}
        assert (
            client.post(
                "/insurance/portal/track/start", json={"request_number": "IR-1", "mobile": "123"}
            ).status_code
            == 422
        )


async def test_attempt_and_rate_limits(db_session):
    with TestClient(app) as client:
        request = _request(client)
        body = {"request_number": request["request_number"], "mobile": "0551234567"}
        code = client.post("/insurance/portal/track/start", json=body).json()["dev_code"]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            client.post("/insurance/portal/track/verify", json={**body, "code": wrong})
        assert (
            client.post("/insurance/portal/track/verify", json={**body, "code": code}).status_code == 401
        )  # locked
        client.post("/insurance/portal/track/start", json=body)
        client.post("/insurance/portal/track/start", json=body)
        assert client.post("/insurance/portal/track/start", json=body).status_code == 429


async def test_status_shows_policy_and_claims_after_issue(db_session):
    from app.api.auth import get_current_staff
    from tests.conftest import _test_manager

    app.dependency_overrides[get_current_staff] = _test_manager
    try:
        with TestClient(app) as client:
            request = _request(client)
            insurer = client.post("/insurance/insurers", json={"name": "شركة أ", "email": "q@a.example"}).json()
            sub = client.post(
                f"/insurance/requests/{request['request_id']}/submissions",
                json={"insurer_ids": [insurer["insurer_id"]]},
            ).json()[0]
            client.post(f"/insurance/submissions/{sub['submission_id']}/approve")
            client.post(f"/insurance/submissions/{sub['submission_id']}/mark-sent", json={})
            quote = client.post(
                f"/insurance/submissions/{sub['submission_id']}/quotes", json={"premium": "2400"}
            ).json()
            client.post(f"/insurance/requests/{request['request_id']}/quotes/{quote['quote_id']}/select")
            policy = client.post(
                f"/insurance/requests/{request['request_id']}/issue-policy",
                json={
                    "installments": 2,
                    "effective_from": f"{(date.today() - timedelta(days=3)).isoformat()}T00:00:00Z",
                },
            ).json()
            client.post(
                "/insurance/claims",
                json={
                    "policy_id": policy["policy_id"],
                    "incident_date": date.today().isoformat(),
                    "description": "حادث بسيط",
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_staff, None)
    with TestClient(app) as client:
        body = {"request_number": request["request_number"], "mobile": "0551234567"}
        code = client.post("/insurance/portal/track/start", json=body).json()["dev_code"]
        token = client.post("/insurance/portal/track/verify", json={**body, "code": code}).json()["token"]
        view = client.get("/insurance/portal/track", headers={"X-Track-Token": token}).json()
    assert view["steps"][4]["current"] and all(s["done"] for s in view["steps"][:4])
    assert view["insurers_contacted"] == 1 and view["quotes_received"] == 1
    assert view["policy"]["policy_number"] == policy["policy_number"] and len(view["policy"]["installments"]) == 2
    assert view["claims"][0]["status"] == "استلمنا البلاغ"
