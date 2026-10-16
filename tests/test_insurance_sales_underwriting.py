from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


async def test_sales_employee_can_assign_underwriting_and_record_preferences(db_session):
    with TestClient(app) as client:
        customer_response = client.post(
            "/insurance/customers",
            json={
                "company_name": "شركة اختبار",
                "customer_type": "business",
                "full_name": "Al Nakhla Logistics",
                "email": "sales@alnakhla.example",
            },
        )
        customer_id = customer_response.json()["customer_id"]

        sales_response = client.post(
            "/insurance/employees",
            json={
                "full_name": "Sara Sales",
                "role": "sales_agent",
                "email": "sara.sales@broker.example",
                "phone": "+966500111222",
                "team": "motor-sales",
            },
        )
        assert sales_response.status_code == 201
        sales_id = sales_response.json()["employee_id"]

        underwriter_response = client.post(
            "/insurance/employees",
            json={
                "full_name": "Nabil Underwriter",
                "role": "underwriter",
                "email": "nabil.underwriter@broker.example",
                "phone": "+966500222333",
                "team": "underwriting",
            },
        )
        assert underwriter_response.status_code == 201
        underwriter_id = underwriter_response.json()["employee_id"]

        request_response = client.post(
            "/insurance/requests",
            json={
                "customer_id": customer_id,
                "insurance_type": "motor",
                "product_type": "private_car",
                "request_source": "sales_agent",
                "coverage_amount": "75000",
                "summary": "Private vehicle for business use.",
            },
        )
        request_id = request_response.json()["request_id"]

        prefs_response = client.post(
            f"/insurance/requests/{request_id}/preferences",
            json={
                "sales_employee_id": sales_id,
                "preferred_insurers": ["SecureDrive", "Amanah"],
                "preferred_premium_range": "1500-2500",
                "preferred_coverage_limit": "80000",
                "customer_priority": "high",
                "notes": "Customer wants fast quote and stable premium.",
            },
        )
        assert prefs_response.status_code == 201

        assignment_response = client.post(
            f"/insurance/requests/{request_id}/assign-underwriting",
            json={
                "sales_employee_id": sales_id,
                "assigned_underwriter_id": underwriter_id,
                "underwriting_status": "under_review",
                "notes": "Customer documents are complete; review for quote readiness.",
            },
        )
        assert assignment_response.status_code == 201
        payload = assignment_response.json()
        assert payload["request_id"] == request_id
        assert payload["assigned_underwriter_id"] == underwriter_id
        assert payload["underwriting_status"] == "under_review"

        read_response = client.get(f"/insurance/requests/{request_id}/underwriting")
        assert read_response.status_code == 200
        assert read_response.json()["assigned_underwriter_id"] == underwriter_id
