"""Required documents per product, automatic review, staff decisions and the signed authorization letter."""

from __future__ import annotations

import os
import struct

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.automation.models import CustomerNotification
from app.insurance import catalog, review
from app.insurance.models import AuthorizationLetter
from app.main import app

DETAILS = {
    "vehicle_make": "تويوتا",
    "vehicle_model": "كامري",
    "vehicle_year": 2022,
    "vehicle_count": 1,
    "vehicle_usage": "خاص",
}
PDF = b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n2 0 obj << /Type /Page >> endobj\n%%EOF"


def png(width: int, height: int, size: int = 60_000) -> bytes:
    header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
    return header + os.urandom(size - len(header))


def keys(docs: list[dict]) -> list[str]:
    return [d["key"] for d in docs]


def test_documents_depend_on_product_and_customer_type():
    individual = keys(catalog.required_documents("motor", "motor_tpl", "individual"))
    assert individual[:2] == ["national_id", "authorization_letter"]
    assert "vehicle_registration" in individual and "vehicle_list" not in individual
    business_fleet = keys(catalog.required_documents("motor", "motor_fleet", "business"))
    assert "commercial_registration" in business_fleet and "national_id" not in business_fleet
    assert "vehicle_list" in business_fleet and "vehicle_registration" not in business_fleet
    assert "employee_census" in keys(catalog.required_documents("medical", "medical_group", "business"))


def test_file_review_rules():
    assert review.review_file(PDF, "application/pdf", "national_id", {"spreadsheet": False})[0] == review.PASSED
    assert "2 صفحة" in review.review_file(PDF, "application/pdf", "x", {})[1][0]
    status, findings = review.review_file(png(400, 300), "image/png", "national_id", {})
    assert status == review.WARNING and any("منخفضة" in f for f in findings)
    assert review.review_file(b"\xff\xd8\xff\x00", "image/jpeg", "x", {})[0] == review.FAILED
    assert review.review_file(PDF, "application/pdf", "customer_upload", None)[0] == review.WARNING
    assert review.review_file(PDF, "application/pdf", "x", {}, duplicate_of="الهوية")[0] == review.WARNING


def test_detail_checks():
    notes = review.detail_findings("motor", "motor_tpl", {**DETAILS, "vehicle_count": 4}, None)
    assert any("أسطول" in n for n in notes)
    notes = review.detail_findings(
        "travel", "travel_single", {"start_date": "2030-05-10", "end_date": "2030-05-01"}, None
    )
    assert any("العودة قبل" in n for n in notes)


def _portal_request(client: TestClient) -> dict:
    response = client.post(
        "/insurance/portal/requests",
        json={
            "full_name": "نورة القحطاني",
            "mobile": "0551234567",
            "insurance_type": "motor",
            "product_type": "motor_tpl",
            "details": DETAILS,
            "consent": True,
        },
    )
    assert response.status_code == 201
    return response.json()


def _upload(client: TestClient, created: dict, document_type: str, name: str, content: bytes) -> dict:
    response = client.post(
        f"/insurance/portal/requests/{created['request_id']}/documents",
        files={"file": (name, content)},
        data={"document_type": document_type},
        headers={"X-Upload-Token": created["upload_token"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _track_token(client: TestClient, created: dict) -> str:
    body = {"request_number": created["request_number"], "mobile": "0551234567"}
    code = client.post("/insurance/portal/track/start", json=body).json()["dev_code"]
    return client.post("/insurance/portal/track/verify", json={**body, "code": code}).json()["token"]


async def test_portal_documents_are_reviewed_and_staff_decide(db_session):
    with TestClient(app) as client:
        created = _portal_request(client)
        uploaded = _upload(client, created, "national_id", "id.png", png(1600, 1000))
        assert uploaded["review_status"] == "passed" and uploaded["document_type"] == "national_id"
        blurry = _upload(client, created, "vehicle_registration", "reg.png", png(500, 320, 20_000))
        assert blurry["review_status"] == "warning"
        # An Excel file is refused where the catalog doesn't ask for a list.
        refused = client.post(
            f"/insurance/portal/requests/{created['request_id']}/documents",
            files={"file": ("x.xlsx", b"PK\x03\x04xl/workbook")},
            data={"document_type": "driving_license"},
            headers={"X-Upload-Token": created["upload_token"]},
        )
        assert refused.status_code == 415

        result = client.get(f"/insurance/requests/{created['request_id']}/review").json()
        items = {i["key"]: i for i in result["items"]}
        assert items["national_id"]["status"] == "passed"
        assert items["vehicle_registration"]["status"] == "warning"
        assert items["driving_license"]["status"] == "missing"
        assert items["authorization_letter"]["status"] == "pending_signature"  # issued with the request
        assert not result["ready"] and "رخصة القيادة" in result["missing"]

        doc_id = items["vehicle_registration"]["documents"][0]["document_id"]
        no_reason = client.post(f"/insurance/documents/{doc_id}/decision", json={"decision": "reject"})
        assert no_reason.status_code == 422
        rejected = client.post(
            f"/insurance/documents/{doc_id}/decision", json={"decision": "reject", "reason": "الصورة غير واضحة"}
        ).json()
        assert {i["key"]: i for i in rejected["items"]}["vehicle_registration"]["status"] == "rejected"

        asked = client.post(f"/insurance/requests/{created['request_id']}/request-documents")
        assert asked.status_code == 200
        request = client.get(f"/insurance/requests/{created['request_id']}").json()
        assert request["status"] == "needs_info"
    message = (
        await db_session.execute(
            select(CustomerNotification).where(CustomerNotification.kind == "documents_missing")
        )
    ).scalar_one()
    assert "رخصة القيادة" in message.body and "استمارة المركبة" in message.body


@pytest.mark.real_auth
async def test_customer_completes_documents_and_signs_the_letter(db_session):
    with TestClient(app) as client:
        created = _portal_request(client)
        token = _track_token(client, created)
        headers = {"X-Track-Token": token}
        view = client.get("/insurance/portal/track", headers=headers).json()
        assert view["letter"]["status"] == "pending_signature" and "أفوّض" in view["letter"]["body_text"]
        assert {d["key"] for d in view["documents"]} >= {"national_id", "vehicle_registration", "driving_license"}

        for key in ("national_id", "vehicle_registration", "driving_license"):
            upload = client.post(
                "/insurance/portal/track/documents",
                files={"file": (f"{key}.pdf", PDF + key.encode())},
                data={"document_type": key},
                headers=headers,
            )
            assert upload.status_code == 201 and upload.json()["review_status"] == "passed"

        sign = {"signer_name": "نورة القحطاني", "id_number": "1012345678", "accept": True}
        assert (
            client.post(
                "/insurance/portal/track/letter/sign", json={**sign, "accept": False}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/insurance/portal/track/letter/sign", json={**sign, "id_number": "123"}, headers=headers
            ).status_code
            == 422
        )
        signed = client.post("/insurance/portal/track/letter/sign", json=sign, headers=headers)
        assert signed.status_code == 200
        code = signed.json()["verification_code"]
        assert client.post("/insurance/portal/track/letter/sign", json=sign, headers=headers).status_code == 409

        view = client.get("/insurance/portal/track", headers=headers).json()
        assert view["letter"]["status"] == "signed" and view["documents_complete"]

        verified = client.get(f"/insurance/portal/verify/{code.lower()}").json()
        assert verified["status"] == "valid" and verified["intact"] and verified["signer_name"] == "نورة القحطاني"
        assert client.get("/insurance/portal/verify/AAAA-BBBB-CCCC").status_code == 404

    letter = (await db_session.execute(select(AuthorizationLetter))).scalar_one()
    assert letter.signer_id_last4 == "5678" and letter.signed_mobile == "+966551234567"
    letter.body_text += " (معدّل)"  # tampering with the stored text breaks the signature
    await db_session.commit()
    with TestClient(app) as client:
        assert client.get(f"/insurance/portal/verify/{code}").json()["intact"] is False


async def test_reissuing_revokes_the_previous_letter(db_session):
    with TestClient(app) as client:
        created = _portal_request(client)
        first = client.get(f"/insurance/requests/{created['request_id']}/review").json()["letter"]
        second = client.post(f"/insurance/requests/{created['request_id']}/authorization-letter").json()["letter"]
        assert second["letter_number"] != first["letter_number"] and second["status"] == "pending_signature"
    letters = (await db_session.execute(select(AuthorizationLetter))).scalars().all()
    assert sorted(item.status.value for item in letters) == ["pending_signature", "revoked"]
    sent = (
        (
            await db_session.execute(
                select(CustomerNotification).where(CustomerNotification.kind == "authorization_letter")
            )
        )
        .scalars()
        .all()
    )
    assert len(sent) == 1 and "track.html" in sent[0].body  # the portal letter waits for the customer's visit
