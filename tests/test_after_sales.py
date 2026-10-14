"""Phase 3: policy book, installments, commissions, renewals, claims, customer files, reports."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.insurance.aftersales import _add_months
from app.insurance.models import SalesEmployee
from app.main import app

DETAILS = {
    "vehicle_make": "تويوتا",
    "vehicle_model": "كامري",
    "vehicle_year": 2022,
    "vehicle_count": 1,
    "vehicle_usage": "خاص",
}


def _issue(
    client: TestClient, premium="4000", installments=1, start: date | None = None, rate=None, mobile="0551234567"
):
    """Portal request → insurer email → quote → select → issue. Returns (policy, request_id, insurer_id)."""
    request = client.post(
        "/insurance/portal/requests",
        json={
            "full_name": "نورة القحطاني",
            "mobile": mobile,
            "insurance_type": "motor",
            "product_type": "motor_comprehensive",
            "details": DETAILS,
            "coverage_amount": "90000",
            "consent": True,
        },
    ).json()
    insurer = client.post(
        "/insurance/insurers", json={"name": f"شركة {mobile}", "email": f"q{mobile}@ins.example"}
    ).json()
    submission = client.post(
        f"/insurance/requests/{request['request_id']}/submissions", json={"insurer_ids": [insurer["insurer_id"]]}
    ).json()[0]
    quote = client.post(
        f"/insurance/submissions/{submission['submission_id']}/quotes",
        json={"premium": premium, "commission_rate": rate},
    ).json()
    client.post(f"/insurance/requests/{request['request_id']}/quotes/{quote['quote_id']}/select")
    body = {"installments": installments}
    if start:
        body["effective_from"] = f"{start.isoformat()}T00:00:00Z"
    response = client.post(f"/insurance/requests/{request['request_id']}/issue-policy", json=body)
    assert response.status_code == 201, response.text
    return response.json(), request["request_id"], insurer["insurer_id"]


async def test_issue_creates_commission_installments_and_one_year_term(db_session):
    with TestClient(app) as client:
        policy, _, _ = _issue(client, premium="10,000", installments=4, start=date(2026, 1, 31), rate="12.5")
        detail = client.get(f"/insurance/portfolio/policies/{policy['policy_id']}").json()
        assert Decimal(detail["commission_rate"]) == Decimal("12.5")
        assert Decimal(detail["commission_amount"]) == Decimal("1250.00")
        assert detail["effective_to"].startswith("2027-01-30T23:59:59")
        due_dates = [i["due_date"] for i in detail["installments"]]
        assert due_dates == ["2026-01-31", "2026-04-30", "2026-07-31", "2026-10-31"]
        assert sum(Decimal(i["amount"]) for i in detail["installments"]) == Decimal("10000.00")
        assert detail["installments_total"] == 4 and detail["installments_paid"] == 0


async def test_uneven_split_puts_rounding_on_last_installment(db_session):
    with TestClient(app) as client:
        policy, _, _ = _issue(client, premium="1000", installments=3)
        amounts = [
            i["amount"]
            for i in client.get(f"/insurance/portfolio/policies/{policy['policy_id']}").json()["installments"]
        ]
        assert amounts == ["333.33", "333.33", "333.34"]
        bad = client.post("/insurance/requests/x/issue-policy", json={"installments": 5})
        assert bad.status_code == 404


async def test_commission_comes_from_the_selected_quote(db_session):
    with TestClient(app) as client:
        no_rate, _, _ = _issue(client)
        assert (
            client.get(f"/insurance/portfolio/policies/{no_rate['policy_id']}").json()["commission_amount"] is None
        )
        url = f"/insurance/policies/{no_rate['policy_id']}/commission-received"
        assert client.post(url, json={}).status_code == 409

        with_rate, request_id, _ = _issue(client, premium="8000", rate="7.5", mobile="0559999999")
        view = client.get(f"/insurance/portfolio/policies/{with_rate['policy_id']}").json()
        assert Decimal(view["commission_rate"]) == Decimal("7.5")
        assert Decimal(view["commission_amount"]) == Decimal("600.00")
        quotes = client.get(f"/insurance/requests/{request_id}/quotes").json()
        assert Decimal(quotes[0]["commission_rate"]) == Decimal("7.5")
        submission_id = quotes[0]["submission_id"]
        bad = client.post(
            f"/insurance/submissions/{submission_id}/quotes", json={"premium": "1", "commission_rate": 150}
        )
        assert bad.status_code == 422


async def test_commission_received_once(db_session):
    with TestClient(app) as client:
        policy, _, _ = _issue(client, rate="10")
        url = f"/insurance/policies/{policy['policy_id']}/commission-received"
        first = client.post(url, json={"reference": "TRX-1"})
        assert first.status_code == 200 and first.json()["commission_reference"] == "TRX-1"
        assert client.post(url, json={}).status_code == 409
        rows = client.get("/insurance/portfolio/commissions").json()
        assert rows[0]["commission_received_at"] is not None


async def test_payments_and_overdue(db_session):
    with TestClient(app) as client:
        start = date.today() - timedelta(days=100)
        policy, _, _ = _issue(client, premium="1200", installments=12, start=start)
        expected_overdue = len([m for m in range(12) if _add_months(start, m) < date.today()])
        overdue = client.get("/insurance/portfolio/installments?status=overdue").json()
        assert len(overdue) == expected_overdue >= 3
        first = overdue[0]
        assert first["customer_name"] == "نورة القحطاني" and first["is_overdue"]
        paid = client.post(
            f"/insurance/installments/{first['installment_id']}/pay",
            json={"payment_method": "sadad", "payment_reference": "S-1"},
        )
        assert paid.json()["status"] == "paid" and paid.json()["is_overdue"] is False
        again = client.post(
            f"/insurance/installments/{first['installment_id']}/pay", json={"payment_method": "sadad"}
        )
        assert again.status_code == 409
        assert (
            client.post(
                f"/insurance/installments/{first['installment_id']}/pay", json={"payment_method": "bitcoin"}
            ).status_code
            == 422
        )
        view = client.get(f"/insurance/portfolio/policies/{policy['policy_id']}").json()
        assert view["installments_paid"] == 1
        assert Decimal(view["overdue_amount"]) == Decimal(100 * (expected_overdue - 1))
        assert Decimal(view["outstanding_amount"]) == Decimal("1100.00")


async def test_renewal_and_cancellation(db_session):
    with TestClient(app) as client:
        policy, request_id, _ = _issue(client, installments=2)
        renewal = client.post(f"/insurance/policies/{policy['policy_id']}/renew")
        assert renewal.status_code == 201
        new_request = client.get(f"/insurance/requests/{renewal.json()['request_id']}").json()
        original = client.get(f"/insurance/requests/{request_id}").json()
        assert new_request["request_source"] == "renewal" and new_request["status"] == "new"
        assert new_request["request_details"] == original["request_details"]
        assert new_request["customer_id"] == original["customer_id"]
        assert client.post(f"/insurance/policies/{policy['policy_id']}/renew").status_code == 409
        view = client.get(f"/insurance/portfolio/policies/{policy['policy_id']}").json()
        assert view["renewal_request_id"] == renewal.json()["request_id"]

        cancelled = client.post(f"/insurance/policies/{policy['policy_id']}/cancel", json={"reason": "طلب العميل"})
        assert cancelled.json()["status"] == "cancelled"
        assert {i["status"] for i in cancelled.json()["installments"]} == {"cancelled"}
        assert Decimal(cancelled.json()["outstanding_amount"]) == 0
        claim = {
            "policy_id": policy["policy_id"],
            "incident_date": date.today().isoformat(),
            "description": "حادث بسيط",
        }
        assert client.post("/insurance/claims", json=claim).status_code == 409


async def test_claim_lifecycle(db_session):
    with TestClient(app) as client:
        start = date.today() - timedelta(days=30)
        policy, _, _ = _issue(client, start=start)
        base = {
            "policy_id": policy["policy_id"],
            "description": "صدمة خلفية عند الإشارة",
            "claimed_amount": "8500",
        }
        before = (start - timedelta(days=1)).isoformat()
        assert client.post("/insurance/claims", json={**base, "incident_date": before}).status_code == 422
        future = (date.today() + timedelta(days=2)).isoformat()
        assert client.post("/insurance/claims", json={**base, "incident_date": future}).status_code == 422

        claim = client.post("/insurance/claims", json={**base, "incident_date": date.today().isoformat()}).json()
        assert claim["status"] == "reported" and claim["allowed_transitions"] == ["closed", "submitted"]
        url = f"/insurance/claims/{claim['claim_id']}/status"
        assert client.post(url, json={"status": "paid"}).status_code == 409
        assert client.post(url, json={"status": "submitted", "insurer_reference": "TAW-77"}).status_code == 200
        assert client.post(url, json={"status": "approved"}).status_code == 422
        approved = client.post(url, json={"status": "approved", "approved_amount": "7000"}).json()
        assert approved["approved_amount"] == "7000.00"
        paid = client.post(url, json={"status": "paid"}).json()
        assert paid["paid_amount"] == "7000.00" and paid["insurer_reference"] == "TAW-77"
        closed = client.post(url, json={"status": "closed"}).json()
        assert closed["allowed_transitions"] == []
        assert client.get(f"/insurance/claims?policy_id={policy['policy_id']}").json()[0]["status"] == "closed"


async def test_customer_file_and_update(db_session):
    with TestClient(app) as client:
        policy, _, _ = _issue(client, premium="3000")
        customer_id = client.get(f"/insurance/portfolio/policies/{policy['policy_id']}").json()["customer_id"]
        client.post(
            "/insurance/claims",
            json={
                "policy_id": policy["policy_id"],
                "incident_date": date.today().isoformat(),
                "description": "كسر زجاج أمامي",
            },
        )
        profile = client.get(f"/insurance/portfolio/customers/{customer_id}").json()
        summary = profile["customer"]
        assert summary["active_policies"] == 1 and summary["open_claims"] == 1
        assert Decimal(summary["total_premium"]) == Decimal("3000.00")
        assert len(profile["requests"]) == 1 and len(profile["policies"]) == 1 and len(profile["claims"]) == 1

        updated = client.patch(
            f"/insurance/customers/{customer_id}", json={"phone": "0501112222", "email": "n@x.example"}
        )
        assert updated.json()["phone"] == "+966501112222"
        assert client.patch(f"/insurance/customers/{customer_id}", json={"phone": "123"}).status_code == 422
        assert client.get("/insurance/portfolio/customers?q=نورة").json()[0]["customer_id"] == customer_id


async def test_report_summary(db_session):
    with TestClient(app) as client:
        start = date.today() - timedelta(days=40)
        policy, _, _ = _issue(client, premium="6000", installments=2, start=start, rate="10")
        _issue(client, premium="4000", mobile="0558888888")
        client.post(
            "/insurance/portal/requests",
            json={  # an unconverted request
                "full_name": "x" * 3,
                "mobile": "0557777777",
                "insurance_type": "travel",
                "product_type": "travel_single",
                "details": {
                    "destination": "دبي",
                    "travelers": 1,
                    "start_date": "2026-12-01",
                    "end_date": "2026-12-05",
                },
                "consent": True,
            },
        )
        claim = client.post(
            "/insurance/claims",
            json={
                "policy_id": policy["policy_id"],
                "incident_date": date.today().isoformat(),
                "description": "سرقة مرآة جانبية",
            },
        ).json()

        report = client.get("/insurance/reports/summary").json()
        assert Decimal(report["premiums_written"]) == Decimal("10000.00")
        assert report["policies_issued"] == 2 and report["requests_created"] == 3
        assert report["conversion_rate"] == pytest.approx(66.7)
        assert Decimal(report["commission_earned"]) == Decimal("600.00")
        assert Decimal(report["commission_pending"]) == Decimal("600.00")
        assert Decimal(report["collections"]["outstanding"]) == Decimal("10000.00")  # nothing paid yet
        assert Decimal(report["collections"]["overdue"]) == Decimal("3000.00")  # first half of the 6000 policy
        assert report["claims"]["open"] == 1 and report["claims"]["by_status"] == {"reported": 1}
        motor = next(row for row in report["by_line"] if row["line"] == "motor")
        assert motor["policies"] == 2 and motor["requests"] == 2
        assert report["monthly"][-1]["month"] == datetime.now(UTC).strftime("%Y-%m")
        assert client.get("/insurance/reports/summary?date_from=2026-05-01&date_to=2026-01-01").status_code == 422
        assert claim["status"] == "reported"


@pytest.mark.real_auth
async def test_sales_cannot_touch_money(db_session):
    db_session.add(
        SalesEmployee(
            full_name="sara",
            email="s@bud.example",
            role="sales_agent",
            password_hash=hash_password("Strong-pass-2026"),
        )
    )
    await db_session.commit()
    with TestClient(app) as client:
        client.post("/auth/login", json={"email": "s@bud.example", "password": "Strong-pass-2026"})
        assert client.get("/insurance/reports/summary").status_code == 403
        assert client.get("/insurance/portfolio/commissions").status_code == 403
        assert client.post("/insurance/installments/x/pay", json={"payment_method": "cash"}).status_code == 403
        assert client.get("/insurance/portfolio/policies").status_code == 200
        assert client.get("/insurance/portfolio/installments").status_code == 200


async def test_claim_documents(db_session):
    with TestClient(app) as client:
        policy, _, _ = _issue(client, start=date.today() - timedelta(days=5))
        claim = client.post(
            "/insurance/claims",
            json={
                "policy_id": policy["policy_id"],
                "incident_date": date.today().isoformat(),
                "description": "حادث تصادم",
            },
        ).json()
        url = f"/insurance/claims/{claim['claim_id']}/documents"
        ok = client.post(
            url,
            files={"file": ("najm.pdf", b"%PDF-1.4 najm", "application/pdf")},
            data={"document_type": "najm_report"},
        )
        assert ok.status_code == 201 and ok.json()["claim_id"] == claim["claim_id"]
        bad = client.post(url, files={"file": ("x.exe", b"MZ...", "application/octet-stream")})
        assert bad.status_code == 415
        docs = client.get(url).json()
        assert [d["file_name"] for d in docs] == ["najm.pdf"]
        download = client.get(f"/insurance/documents/{docs[0]['document_id']}/download")
        assert download.content == b"%PDF-1.4 najm"
