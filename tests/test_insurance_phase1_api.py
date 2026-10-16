from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


async def test_create_customer_and_list_customers(db_session):
    with TestClient(app) as client:
        customer_response = client.post(
            "/insurance/customers",
            json={
                "customer_type": "business",
                "full_name": "Acme Logistics",
                "company_name": "Acme Logistics",
                "email": "ops@acme.example",
                "phone": "+966500000000",
                "notes": "Priority broker follow-up",
            },
        )
        assert customer_response.status_code == 201
        customer_id = customer_response.json()["customer_id"]

        list_response = client.get("/insurance/customers")
        assert list_response.status_code == 200
        assert any(item["customer_id"] == customer_id for item in list_response.json())


async def test_create_request_and_update_status(db_session):
    with TestClient(app) as client:
        customer_response = client.post(
            "/insurance/customers",
            json={
                "customer_type": "business",
                "company_name": "شركة اختبار",
                "full_name": "Sara Ali",
                "email": "sara@test.example",
            },
        )
        customer_id = customer_response.json()["customer_id"]

        request_response = client.post(
            "/insurance/requests",
            json={
                "customer_id": customer_id,
                "insurance_type": "motor",
                "product_type": "private_car",
                "request_source": "portal",
                "coverage_amount": "50000",
                "summary": "Needs a competitive quote for a private sedan",
            },
        )
        assert request_response.status_code == 201
        request_id = request_response.json()["request_id"]

        status_response = client.patch(
            f"/insurance/requests/{request_id}/status",
            json={"status": "under_review", "notes": "Documents received"},
        )
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "under_review"

        list_response = client.get("/insurance/requests", params={"status": "under_review"})
        assert list_response.status_code == 200
        assert any(item["request_id"] == request_id for item in list_response.json())


async def test_upload_document_to_request(db_session):
    with TestClient(app) as client:
        customer_response = client.post(
            "/insurance/customers",
            json={
                "company_name": "شركة اختبار",
                "customer_type": "business",
                "full_name": "Al Noor Group",
                "email": "admin@alnoor.example",
            },
        )
        request_response = client.post(
            "/insurance/requests",
            json={
                "customer_id": customer_response.json()["customer_id"],
                "insurance_type": "property",
                "product_type": "commercial_building",
            },
        )
        request_id = request_response.json()["request_id"]

        upload_response = client.post(
            f"/insurance/requests/{request_id}/documents",
            files={"file": ("passport.pdf", b"fake-pdf-bytes", "application/pdf")},
            data={"document_type": "passport"},
        )
        assert upload_response.status_code == 201
        payload = upload_response.json()
        assert payload["file_name"] == "passport.pdf"
        assert payload["document_type"] == "passport"
