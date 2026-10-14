from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


async def test_create_insurer_template_and_submission_requires_approval(db_session):
    with TestClient(app) as client:
        customer_response = client.post(
            "/insurance/customers",
            json={
                "customer_type": "individual",
                "full_name": "Noura Ahmed",
                "email": "noura@example.com",
            },
        )
        request_response = client.post(
            "/insurance/requests",
            json={
                "customer_id": customer_response.json()["customer_id"],
                "insurance_type": "motor",
                "product_type": "private_car",
                "request_source": "portal",
                "coverage_amount": "45000",
                "summary": "Requesting a motor insurance quote for a private vehicle.",
            },
        )

        insurer_response = client.post(
            "/insurance/insurers",
            json={
                "name": "SecureDrive Insurance",
                "email": "quotes@securedrive.example",
                "contact_name": "Sales Desk",
                "communication_channel": "email",
            },
        )
        assert insurer_response.status_code == 201
        insurer_id = insurer_response.json()["insurer_id"]

        template_response = client.post(
            f"/insurance/insurers/{insurer_id}/templates",
            json={
                "name": "Motor Quote Request",
                "category": "motor",
                "subject_template": "Quote request for {request_number}",
                "body_template": "Hello, please quote for request {request_number}.",
                "required_attachments": ["driver_license", "vehicle_registration"],
            },
        )
        assert template_response.status_code == 201

        submission_response = client.post(
            "/insurance/submissions",
            json={
                "request_id": request_response.json()["request_id"],
                "insurer_id": insurer_id,
                "category": "motor",
                "notes": "Standard quotation request.",
            },
        )
        assert submission_response.status_code == 201
        submission = submission_response.json()
        assert submission["communication_channel"] == "email"
        assert submission["status"] == "pending_approval"

        approval_response = client.post(f"/insurance/submissions/{submission['submission_id']}/approve")
        assert approval_response.status_code == 200
        assert approval_response.json()["status"] == "approved"

        # Email delivery is disabled by default: sending must refuse rather than pretend.
        send_response = client.post(f"/insurance/submissions/{submission['submission_id']}/send")
        assert send_response.status_code == 409
        assert send_response.json()["detail"]["error_code"] == "EMAIL_DELIVERY_DISABLED"
        assert client.get(f"/insurance/submissions/{submission['submission_id']}").json()["status"] == "approved"

        marked = client.post(
            f"/insurance/submissions/{submission['submission_id']}/mark-sent",
            json={"note": "Sent from the underwriter's mailbox"},
        )
        assert marked.status_code == 200
        assert marked.json()["status"] == "sent"


async def test_sales_employee_request_routes_to_underwriting(db_session):
    with TestClient(app) as client:
        employee_response = client.post(
            "/insurance/employees",
            json={
                "full_name": "Sara Salem",
                "role": "sales_agent",
                "email": "sara.sales@example.com",
                "phone": "+966500011111",
                "branch": "Riyadh",
            },
        )
        assert employee_response.status_code == 201
        employee_id = employee_response.json()["employee_id"]

        customer_response = client.post(
            "/insurance/customers",
            json={
                "customer_type": "individual",
                "full_name": "Mohammed Tariq",
                "email": "mohammed@example.com",
                "phone": "+966500022222",
            },
        )
        assert customer_response.status_code == 201
        customer_id = customer_response.json()["customer_id"]

        request_response = client.post(
            "/insurance/requests",
            json={
                "customer_id": customer_id,
                "sales_employee_id": employee_id,
                "insurance_type": "motor",
                "product_type": "private_car",
                "request_source": "sales_agent",
                "coverage_amount": "120000",
                "summary": "Customer requests motor coverage for a sedan.",
                "preferred_insurers": ["SecureDrive", "NordCover"],
                "premium_budget": "8000",
                "coverage_limit": "150000",
                "customer_priority": "high",
            },
        )
        assert request_response.status_code == 201
        request_id = request_response.json()["request_id"]
        assert request_response.json()["sales_employee_id"] == employee_id
        assert request_response.json()["preferred_insurers"] == ["SecureDrive", "NordCover"]

        upload_response = client.post(
            f"/insurance/requests/{request_id}/documents",
            files={"file": ("license.pdf", b"license-content", "application/pdf")},
            data={
                "document_type": "driver_license",
                "uploaded_by_employee_id": employee_id,
            },
        )
        assert upload_response.status_code == 201

        assign_response = client.post(
            f"/insurance/requests/{request_id}/assign-underwriting",
            json={
                "underwriter_id": employee_id,
                "notes": "Customer has high priority and full document set.",
            },
        )
        assert assign_response.status_code == 200
        assignment = assign_response.json()
        assert assignment["status"] == "assigned"
        assert assignment["request_id"] == request_id


async def test_quote_capture_and_best_quote_selection(db_session):
    with TestClient(app) as client:
        customer_response = client.post(
            "/insurance/customers",
            json={
                "customer_type": "individual",
                "full_name": "Aisha Rahman",
                "email": "aisha@example.com",
            },
        )
        request_response = client.post(
            "/insurance/requests",
            json={
                "customer_id": customer_response.json()["customer_id"],
                "insurance_type": "motor",
                "product_type": "private_car",
                "coverage_amount": "50000",
                "summary": "Compare two motor quotes",
            },
        )
        insurer_one = client.post(
            "/insurance/insurers",
            json={
                "name": "Alpha Cover",
                "email": "alpha@quotes.example",
                "communication_channel": "email",
            },
        )
        insurer_two = client.post(
            "/insurance/insurers",
            json={
                "name": "Beta Cover",
                "email": "beta@quotes.example",
                "communication_channel": "email",
            },
        )
        submission_one = client.post(
            "/insurance/submissions",
            json={
                "request_id": request_response.json()["request_id"],
                "insurer_id": insurer_one.json()["insurer_id"],
                "category": "motor",
            },
        )
        submission_two = client.post(
            "/insurance/submissions",
            json={
                "request_id": request_response.json()["request_id"],
                "insurer_id": insurer_two.json()["insurer_id"],
                "category": "motor",
            },
        )

        quote_one = client.post(
            f"/insurance/submissions/{submission_one.json()['submission_id']}/quotes",
            json={
                "premium": "1800",
                "coverage_limit": "80000",
                "conditions": "Basic cover",
                "validity_date": "2026-12-31T00:00:00Z",
            },
        )
        assert quote_one.status_code == 201

        quote_two = client.post(
            f"/insurance/submissions/{submission_two.json()['submission_id']}/quotes",
            json={
                "premium": "1500",
                "coverage_limit": "90000",
                "conditions": "Expanded cover",
                "validity_date": "2026-12-31T00:00:00Z",
            },
        )
        assert quote_two.status_code == 201

        quote_list = client.get(f"/insurance/requests/{request_response.json()['request_id']}/quotes")
        assert quote_list.status_code == 200
        assert len(quote_list.json()) == 2

        best_quote = client.get(f"/insurance/requests/{request_response.json()['request_id']}/best-quote")
        assert best_quote.status_code == 200
        assert best_quote.json()["premium"] == "1500"


async def test_select_quote_for_request_and_store_selection(db_session):
    with TestClient(app) as client:
        customer_response = client.post(
            "/insurance/customers",
            json={
                "customer_type": "individual",
                "full_name": "Layla Hassan",
                "email": "layla@example.com",
            },
        )
        request_response = client.post(
            "/insurance/requests",
            json={
                "customer_id": customer_response.json()["customer_id"],
                "insurance_type": "health",
                "product_type": "family",
                "coverage_amount": "200000",
                "summary": "Family health comparison",
            },
        )
        insurer_response = client.post(
            "/insurance/insurers",
            json={
                "name": "CarePlus",
                "email": "careplus@example.com",
                "communication_channel": "email",
            },
        )
        submission_response = client.post(
            "/insurance/submissions",
            json={
                "request_id": request_response.json()["request_id"],
                "insurer_id": insurer_response.json()["insurer_id"],
                "category": "health",
            },
        )
        quote_response = client.post(
            f"/insurance/submissions/{submission_response.json()['submission_id']}/quotes",
            json={
                "premium": "2200",
                "coverage_limit": "250000",
                "conditions": "Family health cover",
                "validity_date": "2026-12-31T00:00:00Z",
            },
        )
        assert quote_response.status_code == 201

        select_response = client.post(
            f"/insurance/requests/{request_response.json()['request_id']}/quotes/{quote_response.json()['quote_id']}/select"
        )
        assert select_response.status_code == 200
        assert select_response.json()["quote_id"] == quote_response.json()["quote_id"]

        selected = client.get(f"/insurance/requests/{request_response.json()['request_id']}/selected-quote")
        assert selected.status_code == 200
        assert selected.json()["quote_id"] == quote_response.json()["quote_id"]


async def test_issue_policy_after_selected_quote(db_session):
    with TestClient(app) as client:
        customer_response = client.post(
            "/insurance/customers",
            json={
                "customer_type": "individual",
                "full_name": "Zayd Ali",
                "email": "zayd@example.com",
            },
        )
        request_response = client.post(
            "/insurance/requests",
            json={
                "customer_id": customer_response.json()["customer_id"],
                "insurance_type": "home",
                "product_type": "condo",
                "coverage_amount": "350000",
                "summary": "Condo insurance comparison",
            },
        )
        insurer_response = client.post(
            "/insurance/insurers",
            json={
                "name": "NorthStar Cover",
                "email": "northstar@example.com",
                "communication_channel": "email",
            },
        )
        submission_response = client.post(
            "/insurance/submissions",
            json={
                "request_id": request_response.json()["request_id"],
                "insurer_id": insurer_response.json()["insurer_id"],
                "category": "home",
            },
        )
        quote_response = client.post(
            f"/insurance/submissions/{submission_response.json()['submission_id']}/quotes",
            json={
                "premium": "4200",
                "coverage_limit": "500000",
                "conditions": "Named peril policy",
                "validity_date": "2026-12-31T00:00:00Z",
            },
        )
        assert quote_response.status_code == 201

        select_response = client.post(
            f"/insurance/requests/{request_response.json()['request_id']}/quotes/{quote_response.json()['quote_id']}/select"
        )
        assert select_response.status_code == 200

        issue_response = client.post(
            f"/insurance/requests/{request_response.json()['request_id']}/issue-policy",
            json={
                "effective_from": "2026-10-10T00:00:00Z",
                "effective_to": "2027-10-10T00:00:00Z",
                "notes": "Issued after selected quote approval",
            },
        )
        assert issue_response.status_code == 201
        payload = issue_response.json()
        assert payload["request_id"] == request_response.json()["request_id"]
        assert payload["selected_quote_id"] == quote_response.json()["quote_id"]
        assert payload["status"] == "issued"

        fetched = client.get(f"/insurance/requests/{request_response.json()['request_id']}/policy")
        assert fetched.status_code == 200
        assert fetched.json()["policy_number"] == payload["policy_number"]

        activate_response = client.post(f"/insurance/policies/{payload['policy_id']}/activate")
        assert activate_response.status_code == 200
        assert activate_response.json()["status"] == "active"


async def test_send_submission_uses_email_and_pdf_attachment(db_session, monkeypatch):
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            self.host = host
            self.port = port
            self.sent = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self):
            pass

        def login(self, username, password):
            captured["login"] = (username, password)

        def send_message(self, message):
            captured["message"] = message
            self.sent = message

        def quit(self):
            pass

    monkeypatch.setattr("app.api.routes.insurance.smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("app.api.routes.insurance.settings.smtp_host", "smtp.example.com")
    monkeypatch.setattr("app.api.routes.insurance.settings.smtp_port", 587)
    monkeypatch.setattr("app.api.routes.insurance.settings.smtp_username", "mailer@example.com")
    monkeypatch.setattr("app.api.routes.insurance.settings.smtp_password", "secret")
    monkeypatch.setattr("app.api.routes.insurance.settings.smtp_from_email", "broker@example.com")
    monkeypatch.setattr("app.api.routes.insurance.settings.email_delivery_enabled", True)

    with TestClient(app) as client:
        customer = client.post(
            "/insurance/customers",
            json={
                "customer_type": "individual",
                "full_name": "Samira Noor",
                "email": "samira@example.com",
            },
        )
        request = client.post(
            "/insurance/requests",
            json={
                "customer_id": customer.json()["customer_id"],
                "insurance_type": "motor",
                "product_type": "private_car",
                "coverage_amount": "60000",
                "summary": "Request for motor quotation with supporting license PDF.",
            },
        )
        insurer = client.post(
            "/insurance/insurers",
            json={
                "name": "MetroCover",
                "email": "quotes@metrocover.example",
                "contact_name": "Claims Desk",
                "communication_channel": "email",
            },
        )
        submission = client.post(
            "/insurance/submissions",
            json={
                "request_id": request.json()["request_id"],
                "insurer_id": insurer.json()["insurer_id"],
                "category": "motor",
            },
        )
        doc = client.post(
            f"/insurance/requests/{request.json()['request_id']}/documents",
            files={"file": ("driver_license.pdf", b"pdf-bytes", "application/pdf")},
            data={"document_type": "driver_license"},
        )
        assert doc.status_code == 201

        approver = client.post(f"/insurance/submissions/{submission.json()['submission_id']}/approve")
        assert approver.status_code == 200

        response = client.post(f"/insurance/submissions/{submission.json()['submission_id']}/send")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "sent"
        assert captured["message"].get("To") == "quotes@metrocover.example"
        assert any(part.get_filename() == "driver_license.pdf" for part in captured["message"].iter_attachments())
