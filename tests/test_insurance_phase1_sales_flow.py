"""Phase 1: portal request → underwriting emails (templates) → quotes → policy, end to end."""

from __future__ import annotations

import smtplib

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app

MOTOR_DETAILS = {
    "vehicle_make": "تويوتا",
    "vehicle_model": "كامري",
    "vehicle_year": 2022,
    "vehicle_count": 1,
    "vehicle_usage": "خاص",
}


def _portal_request(client: TestClient, **overrides) -> dict:
    payload = {
        "customer_type": "individual",
        "full_name": "نورة القحطاني",
        "mobile": "0551234567",
        "email": "noura@example.com",
        "insurance_type": "motor",
        "product_type": "motor_comprehensive",
        "details": MOTOR_DETAILS,
        "coverage_amount": "90000",
        "consent": True,
        **overrides,
    }
    response = client.post("/insurance/portal/requests", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _insurer(client: TestClient, name: str, email: str) -> str:
    response = client.post("/insurance/insurers", json={"name": name, "email": email})
    assert response.status_code == 201
    return response.json()["insurer_id"]


def test_catalog_covers_every_line_with_products_and_fields():
    with TestClient(app) as client:
        lines = client.get("/insurance/catalog").json()["lines"]
    keys = {line["key"] for line in lines}
    assert {"motor", "medical", "property", "engineering", "marine", "liability", "travel"} <= keys
    assert all(line["products"] and line["fields"] for line in lines)


async def test_portal_request_creates_customer_and_normalizes_mobile(db_session):
    with TestClient(app) as client:
        created = _portal_request(client)
        assert created["status"] == "new"
        again = _portal_request(client, mobile="+966 55 123 4567")  # same customer, different spelling

        request = client.get(f"/insurance/requests/{created['request_id']}").json()
        assert request["customer_name"] == "نورة القحطاني"
        assert request["insurance_type_label"] == "المركبات"
        assert request["product_type_label"] == "شامل"
        assert request["request_details"]["vehicle_make"] == "تويوتا"
        assert request["request_source"] == "portal"
        customers = client.get("/insurance/customers").json()
        assert [c["phone"] for c in customers] == ["+966551234567"]
        assert (
            client.get(f"/insurance/requests/{again['request_id']}").json()["customer_id"]
            == request["customer_id"]
        )


async def test_portal_rejects_incomplete_details_bad_mobile_and_missing_consent(db_session):
    with TestClient(app) as client:
        base = {
            "full_name": "x" * 3,
            "mobile": "0551234567",
            "insurance_type": "motor",
            "product_type": "motor_tpl",
            "details": MOTOR_DETAILS,
            "consent": True,
        }
        missing = client.post("/insurance/portal/requests", json={**base, "details": {"vehicle_make": "x"}})
        assert missing.status_code == 422
        assert "سنة الصنع" in missing.json()["detail"]["message"]
        assert client.post("/insurance/portal/requests", json={**base, "mobile": "12345"}).status_code == 422
        assert client.post("/insurance/portal/requests", json={**base, "consent": False}).status_code == 422
        assert client.post("/insurance/portal/requests", json={**base, "product_type": "nope"}).status_code == 422


async def test_status_transitions_are_enforced(db_session):
    with TestClient(app) as client:
        request_id = _portal_request(client)["request_id"]
        url = f"/insurance/requests/{request_id}/status"

        assert client.patch(url, json={"status": "needs_info"}).status_code == 200
        blocked = client.patch(url, json={"status": "policy_issued"})
        assert blocked.status_code == 409
        assert client.patch(url, json={"status": "closed"}).json()["allowed_transitions"] == ["under_review"]
        assert client.patch(url, json={"status": "ready_for_quote"}).status_code == 409
        assert client.patch(url, json={"status": "under_review"}).status_code == 200


async def test_full_flow_with_default_and_insurer_templates(db_session, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, *args):
            pass

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr("app.api.routes.insurance.smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("app.api.routes.insurance.settings.email_delivery_enabled", True)

    with TestClient(app) as client:
        request_id = _portal_request(client)["request_id"]
        tawuniya = _insurer(client, "شركة أ", "quotes@a.example")
        walaa = _insurer(client, "شركة ب", "quotes@b.example")

        default = client.post(
            "/insurance/templates",
            json={
                "name": "افتراضي المركبات",
                "category": "motor",
                "subject_template": "طلب تسعير {request_number}",
                "body_template": "إلى {insurer_name}\n{risk_details}",
            },
        )
        assert default.status_code == 201 and default.json()["insurer_id"] is None
        override = client.post(
            f"/insurance/insurers/{walaa}/templates",
            json={
                "name": "قالب ب",
                "category": "motor",
                "subject_template": "B-{request_number}",
                "body_template": "Dear {insurer_contact}",
            },
        )
        assert override.status_code == 201
        bad = client.post(
            "/insurance/templates",
            json={
                "name": "خطأ",
                "category": "motor",
                "subject_template": "x {typo}",
                "body_template": "body",
            },
        )
        assert bad.status_code == 422

        drafts = client.post(
            f"/insurance/requests/{request_id}/submissions", json={"insurer_ids": [tawuniya, walaa]}
        )
        assert drafts.status_code == 201
        by_insurer = {d["insurer_id"]: d for d in drafts.json()}
        assert by_insurer[tawuniya]["subject"].startswith("طلب تسعير IR-")
        assert "الشركة المصنعة: تويوتا" in by_insurer[tawuniya]["email_body"]
        assert by_insurer[walaa]["subject"].startswith("B-IR-")
        assert client.get(f"/insurance/requests/{request_id}").json()["status"] == "ready_for_quote"

        # Editing an approved email sends it back for approval.
        sub_a = by_insurer[tawuniya]["submission_id"]
        client.post(f"/insurance/submissions/{sub_a}/approve")
        edited = client.patch(f"/insurance/submissions/{sub_a}", json={"email_body": "نص معدل"})
        assert edited.json()["status"] == "pending_approval" and edited.json()["approved_for_send"] is False
        assert client.post(f"/insurance/submissions/{sub_a}/send").status_code == 400
        client.post(f"/insurance/submissions/{sub_a}/approve")
        assert client.post(f"/insurance/submissions/{sub_a}/send").json()["status"] == "sent"
        assert sent[-1]["To"] == "quotes@a.example" and sent[-1].get_content().strip() == "نص معدل"
        assert client.patch(f"/insurance/submissions/{sub_a}", json={"email_body": "late"}).status_code == 409

        quote = client.post(
            f"/insurance/submissions/{sub_a}/quotes", json={"premium": "2400", "coverage_limit": "90000"}
        )
        quote_id = quote.json()["quote_id"]
        assert client.post(f"/insurance/requests/{request_id}/quotes/{quote_id}/select").status_code == 200
        assert client.get(f"/insurance/requests/{request_id}").json()["status"] == "quote_selected"

        policy = client.post(f"/insurance/requests/{request_id}/issue-policy", json={})
        assert policy.status_code == 201
        final = client.get(f"/insurance/requests/{request_id}").json()
        assert final["status"] == "policy_issued" and final["allowed_transitions"] == []


async def test_template_preview_with_real_request(db_session):
    with TestClient(app) as client:
        request_id = _portal_request(client)["request_id"]
        insurer_id = _insurer(client, "شركة ج", "c@c.example")
        preview = client.post(
            "/insurance/templates/preview",
            json={
                "subject_template": "{insurance_type} / {product_type}",
                "body_template": "{customer_name} {unknown_thing}",
                "request_id": request_id,
                "insurer_id": insurer_id,
            },
        ).json()
        assert preview["subject"] == "المركبات / شامل"
        assert preview["body"] == "نورة القحطاني {unknown_thing}"
        assert preview["unknown_placeholders"] == ["unknown_thing"]


async def test_smtp_failure_is_recorded_not_reported_as_sent(db_session, monkeypatch):
    class BrokenSMTP:
        def __init__(self, *args, **kwargs):
            raise smtplib.SMTPConnectError(421, "service not available")

    monkeypatch.setattr("app.api.routes.insurance.smtplib.SMTP", BrokenSMTP)
    monkeypatch.setattr("app.api.routes.insurance.settings.email_delivery_enabled", True)
    with TestClient(app) as client:
        request_id = _portal_request(client)["request_id"]
        insurer_id = _insurer(client, "شركة د", "d@d.example")
        submission = client.post(
            f"/insurance/requests/{request_id}/submissions", json={"insurer_ids": [insurer_id]}
        ).json()[0]
        client.post(f"/insurance/submissions/{submission['submission_id']}/approve")
        result = client.post(f"/insurance/submissions/{submission['submission_id']}/send").json()
        assert result["status"] == "failed"
        assert "SMTPConnectError" in result["delivery_error"]
        assert result["sent_at"] is None


async def test_upload_cannot_escape_uploads_folder(db_session):
    with TestClient(app) as client:
        request_id = _portal_request(client)["request_id"]
        response = client.post(
            f"/insurance/requests/{request_id}/documents",
            files={"file": ("../../../evil.txt", b"x", "text/plain")},
            data={"document_type": "other"},
        )
        assert response.status_code == 201
        assert response.json()["file_name"] == "evil.txt"
        assert response.json()["storage_key"].startswith(f"app/static/uploads/{request_id}/")


async def test_statuses_are_stored_as_lowercase_values(db_session):
    with TestClient(app) as client:
        _portal_request(client)
    stored = (await db_session.execute(text("SELECT status FROM insurance_requests"))).scalars().all()
    assert stored == ["new"]
