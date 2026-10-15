"""Sales cycle automation: assignment, hand-off to underwriting once complete, and reminders."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.automation import jobs
from app.automation.models import AutomationRule, CustomerNotification
from app.insurance.models import InsuranceRequest, SalesEmployee
from app.main import app
from tests.test_documents_review import PDF, _portal_request, _track_token, png

MOTOR_TPL_DOCS = ("national_id", "vehicle_registration", "driving_license")
SIGN = {"signer_name": "نورة القحطاني", "id_number": "1012345678", "accept": True}


async def _staff(db_session, name: str, role: str) -> str:
    employee = SalesEmployee(full_name=name, email=f"{name}@bud.example", role=role)
    db_session.add(employee)
    await db_session.commit()
    return employee.employee_id


async def _request_row(db_session, request_id: str) -> InsuranceRequest:
    db_session.expire_all()
    request = await db_session.get(InsuranceRequest, request_id)
    assert request is not None
    return request


async def _outbox(db_session, kind: str) -> list[CustomerNotification]:
    result = await db_session.execute(select(CustomerNotification).where(CustomerNotification.kind == kind))
    return list(result.scalars())


def _upload_from_tracking(client: TestClient, headers: dict, key: str, content: bytes) -> None:
    response = client.post(
        "/insurance/portal/track/documents",
        files={"file": (f"{key}.pdf", content)},
        data={"document_type": key},
        headers=headers,
    )
    assert response.status_code == 201, response.text


async def test_new_requests_go_to_the_least_busy_sales_agent(db_session):
    busy = await _staff(db_session, "a-busy", "sales_agent")
    free = await _staff(db_session, "b-free", "sales_agent")
    with TestClient(app) as client:
        first = _portal_request(client)
        assert (await _request_row(db_session, first["request_id"])).sales_employee_id == busy  # tie: by name
        second = _portal_request(client)
        assert (await _request_row(db_session, second["request_id"])).sales_employee_id == free
        assert (
            client.put("/insurance/automation/rules/auto_assign_sales", json={"enabled": False}).status_code == 200
        )
        third = _portal_request(client)
    assert (await _request_row(db_session, third["request_id"])).sales_employee_id is None


async def test_complete_request_is_handed_to_underwriting(db_session):
    underwriter = await _staff(db_session, "uw", "underwriter")
    with TestClient(app) as client:
        created = _portal_request(client)
        headers = {"X-Track-Token": _track_token(client, created)}
        for key in MOTOR_TPL_DOCS:
            _upload_from_tracking(client, headers, key, PDF + key.encode())
        assert (await _request_row(db_session, created["request_id"])).status.value == "new"  # letter unsigned
        assert client.post("/insurance/portal/track/letter/sign", json=SIGN, headers=headers).status_code == 200

    request = await _request_row(db_session, created["request_id"])
    assert request.status.value == "under_review" and request.underwriter_id == underwriter


async def test_a_remark_waits_for_staff_before_the_hand_off(db_session):
    await _staff(db_session, "uw", "underwriter")
    with TestClient(app) as client:
        created = _portal_request(client)
        headers = {"X-Track-Token": _track_token(client, created)}
        _upload_from_tracking(client, headers, "national_id", PDF + b"id")
        _upload_from_tracking(client, headers, "driving_license", PDF + b"lic")
        blurry = client.post(
            "/insurance/portal/track/documents",
            files={"file": ("reg.png", png(500, 300, 20_000))},
            data={"document_type": "vehicle_registration"},
            headers=headers,
        )
        assert blurry.json()["review_status"] == "warning"
        client.post("/insurance/portal/track/letter/sign", json=SIGN, headers=headers)
        assert (await _request_row(db_session, created["request_id"])).status.value == "new"

        review = client.get(f"/insurance/requests/{created['request_id']}/review").json()
        reg = next(i for i in review["items"] if i["key"] == "vehicle_registration")["documents"][0]
        client.post(f"/insurance/documents/{reg['document_id']}/decision", json={"decision": "accept"})
    assert (await _request_row(db_session, created["request_id"])).status.value == "under_review"


async def test_reminders_for_signature_and_missing_documents(db_session):
    with TestClient(app) as client:
        created = _portal_request(client)
    start = datetime.now(UTC)

    first = await jobs.sales_cycle(db_session, start + timedelta(hours=25))
    assert first["signature_reminders"] == 1 and first["documents_reminders"] == 0
    assert (await jobs.sales_cycle(db_session, start + timedelta(hours=26)))["signature_reminders"] == 0  # once

    later = await jobs.sales_cycle(db_session, start + timedelta(hours=49))
    assert later["signature_reminders"] == 0 and later["documents_reminders"] == 1
    assert (await jobs.sales_cycle(db_session, start + timedelta(hours=73)))["signature_reminders"] == 1
    await db_session.commit()

    sign = await _outbox(db_session, "signature_reminder")
    docs = await _outbox(db_session, "documents_reminder")
    assert len(sign) == 2 and created["request_number"] in sign[0].body and "track.html" in sign[0].body
    assert len(docs) == 1 and "رخصة القيادة" in docs[0].body and "خطاب" not in docs[0].body


async def test_reminders_can_be_switched_off_and_retimed(db_session):
    db_session.add(AutomationRule(key="signature_reminder", enabled=False))
    await db_session.commit()
    with TestClient(app) as client:
        _portal_request(client)
        bad = client.put("/insurance/automation/rules/documents_reminder", json={"params": {"after_hours": [0]}})
        assert bad.status_code == 422
        ok = client.put("/insurance/automation/rules/documents_reminder", json={"params": {"after_hours": [2]}})
        assert ok.status_code == 200
    counts = await jobs.sales_cycle(db_session, datetime.now(UTC) + timedelta(hours=3))
    assert counts["signature_reminders"] == 0 and counts["documents_reminders"] == 1
