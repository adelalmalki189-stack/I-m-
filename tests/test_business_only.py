"""BUD serves businesses only: CR number required, one customer file per business, and each
request's filer is the one who tracks it, receives its messages and signs its letter."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.automation.models import CustomerNotification
from app.insurance.models import Customer
from app.main import app
from tests.test_documents_review import DETAILS

BASE = {
    "company_name": "شركة النخبة للتجارة",
    "cr_number": "1010555555",
    "insurance_type": "motor",
    "product_type": "motor_tpl",
    "details": DETAILS,
    "consent": True,
}


def _file(client: TestClient, name: str, mobile: str, **extra) -> dict:
    response = client.post(
        "/insurance/portal/requests", json={**BASE, "full_name": name, "mobile": mobile, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _token(client: TestClient, number: str, mobile: str) -> str:
    body = {"request_number": number, "mobile": mobile}
    code = client.post("/insurance/portal/track/start", json=body).json().get("dev_code")
    if code is None:
        return ""
    response = client.post("/insurance/portal/track/verify", json={**body, "code": code})
    return response.json().get("token", "") if response.status_code == 200 else ""


async def test_individuals_and_missing_company_details_are_refused(db_session):
    with TestClient(app) as client:
        person = {**BASE, "full_name": "فرد", "mobile": "0551234567", "customer_type": "individual"}
        assert client.post("/insurance/portal/requests", json=person).status_code == 422
        for key, bad in (("company_name", ""), ("cr_number", "123"), ("cr_number", "9010555555")):
            payload = {**BASE, "full_name": "مقدم", "mobile": "0551234567", key: bad}
            assert client.post("/insurance/portal/requests", json=payload).status_code == 422
        bad_vat = {**BASE, "full_name": "مقدم", "mobile": "0551234567", "vat_number": "123456789012345"}
        assert client.post("/insurance/portal/requests", json=bad_vat).status_code == 422
        staff = client.post("/insurance/customers", json={"full_name": "فرد", "customer_type": "individual"})
        assert staff.status_code == 422


async def test_one_business_two_filers(db_session):
    with TestClient(app) as client:
        first = _file(
            client, "سارة", "0551111111", contact_title="مديرة الموارد البشرية", vat_number="300000000000003"
        )
        second = _file(client, "فهد", "0552222222", contact_title="مدير المشتريات")

        # Each filer follows only their own request, with their own mobile.
        assert _token(client, second["request_number"], "0552222222")
        assert not _token(client, second["request_number"], "0551111111")
        token = _token(client, first["request_number"], "0551111111")
        view = client.get("/insurance/portal/track", headers={"X-Track-Token": token}).json()
        letter = view["letter"]["body_text"]
        assert (
            "نحن شركة النخبة للتجارة، سجل تجاري رقم 1010555555" in letter
            and "سارة (مديرة الموارد البشرية)" in letter
        )

        sign = {
            "signer_name": "سارة",
            "id_number": "1012345678",
            "signer_title": "مديرة الموارد البشرية",
            "accept": True,
        }
        assert (
            client.post(
                "/insurance/portal/track/letter/sign",
                json={**sign, "signer_title": ""},
                headers={"X-Track-Token": token},
            ).status_code
            == 422
        )
        signed = client.post("/insurance/portal/track/letter/sign", json=sign, headers={"X-Track-Token": token})
        assert signed.status_code == 200
        verified = client.get(f"/insurance/portal/verify/{signed.json()['verification_code']}").json()
        assert verified["intact"]

        staff_view = client.get(f"/insurance/requests/{second['request_id']}").json()
        assert staff_view["contact_name"] == "فهد" and staff_view["contact_mobile"] == "+966552222222"

    customers = (await db_session.execute(select(Customer))).scalars().all()
    assert (
        len(customers) == 1
        and customers[0].cr_number == "1010555555"
        and customers[0].vat_number == "300000000000003"
    )
    codes = (
        (await db_session.execute(select(CustomerNotification).where(CustomerNotification.kind == "otp")))
        .scalars()
        .all()
    )
    by_request = {(n.request_id, n.to_phone) for n in codes}
    assert (second["request_id"], "+966552222222") in by_request
    assert (first["request_id"], "+966551111111") in by_request
