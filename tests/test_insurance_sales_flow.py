from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


async def test_create_sales_agent_and_assign_request(db_session):
    with TestClient(app) as client:
        customer_response = client.post(
            "/insurance/customers",
            json={
                "customer_type": "business",
                "full_name": "Al Saad Trading",
                "company_name": "Al Saad Trading",
                "email": "sales@alsaad.example",
                "phone": "+966500123456",
            },
        )
        assert customer_response.status_code == 201

        sales_agent_response = client.post(
            "/insurance/sales-agents",
            json={
                "full_name": "Sara Rahman",
                "email": "sara@broker.example",
                "phone": "+966500987654",
                "role": "sales_agent",
                "branch": "Riyadh",
            },
        )
        assert sales_agent_response.status_code == 201
        sales_agent_id = sales_agent_response.json()["sales_agent_id"]

        request_response = client.post(
            "/insurance/requests",
            json={
                "customer_id": customer_response.json()["customer_id"],
                "sales_agent_id": sales_agent_id,
                "insurance_type": "motor",
                "product_type": "private_car",
                "coverage_amount": "60000",
                "preferred_insurer": "SecureDrive",
                "preferred_premium_range": "1800-2500",
                "customer_preferences": "Prefer low deductible and quick approval",
                "sales_notes": "Customer wants an annual renewal with flexible payment",
            },
        )
        assert request_response.status_code == 201
        payload = request_response.json()
        assert payload["sales_agent_id"] == sales_agent_id
        assert payload["preferred_insurer"] == "SecureDrive"
        assert payload["customer_preferences"] == "Prefer low deductible and quick approval"
