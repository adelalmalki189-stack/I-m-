"""Payment request after the customer accepts, transfer proof, confirmation, then the policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.automation import jobs
from app.automation.models import CustomerNotification
from app.core.config import settings
from app.insurance.models import PaymentRequest
from app.main import app
from tests.test_documents_review import PDF, _track_token
from tests.test_offers import SIGN, _quoted_request


def _accepted(client: TestClient, premium: str = "12000") -> tuple[dict, dict, dict]:
    created, quotes = _quoted_request(client, [{"premium": premium}])
    client.post(f"/insurance/requests/{created['request_id']}/offers", json={"quote_ids": [quotes[0]["quote_id"]]})
    headers = {"X-Track-Token": _track_token(client, created)}
    accepted = client.post(
        "/insurance/portal/track/offers/accept", json={**SIGN, "quote_id": quotes[0]["quote_id"]}, headers=headers
    )
    assert accepted.status_code == 200
    return created, quotes[0], headers


async def _kinds(db_session) -> list[str]:
    return [n.kind for n in (await db_session.execute(select(CustomerNotification))).scalars()]


async def test_pay_then_issue(db_session, monkeypatch):
    monkeypatch.setattr(settings, "payment_bank_name", "البنك التجريبي")
    monkeypatch.setattr(settings, "payment_account_name", "بعد للوساطة")
    monkeypatch.setattr(settings, "payment_iban", "SA03 8000 0000 6080 1016 7519")
    monkeypatch.setattr(settings, "payment_link_template", "https://pay.example/{reference}?amount={amount}")
    with TestClient(app) as client:
        created, quote, headers = _accepted(client)
        request_id = created["request_id"]
        payment = client.get("/insurance/portal/track", headers=headers).json()["payment"]
        assert payment["status"] == "issued" and Decimal(payment["amount_due"]) == Decimal("12000.00")
        assert payment["bank"]["iban"] == "SA0380000000608010167519"
        assert payment["payment_link"] == f"https://pay.example/{payment['payment_number']}?amount=12000.00"
        assert payment["due_date"] == (datetime.now(UTC).date() + timedelta(days=7)).isoformat()
        notice = client.get("/insurance/portal/track/payment/document?kind=notice", headers=headers)
        assert (
            notice.status_code == 200
            and payment["payment_number"] in notice.text
            and "SA0380000000608010167519" in notice.text
        )

        blocked = client.post(f"/insurance/requests/{request_id}/issue-policy", json={})
        assert blocked.status_code == 409 and blocked.json()["detail"]["error_code"] == "PAYMENT_PENDING"

        bad = client.post(
            "/insurance/portal/track/payment/proof", files={"file": ("x.txt", b"hi")}, headers=headers
        )
        assert bad.status_code == 415
        proof = client.post(
            "/insurance/portal/track/payment/proof", files={"file": ("r.pdf", PDF)}, headers=headers
        )
        assert proof.status_code == 201 and proof.json()["status"] == "proof_submitted"
        pending = client.get("/insurance/payment-requests?status=proof_submitted").json()
        assert [p["request_id"] for p in pending] == [request_id] and pending[0]["proof_document_id"]
        rejected = client.post(
            f"/insurance/payment-requests/{payment['payment_id']}/reject-proof", json={"reason": "المبلغ ناقص"}
        )
        assert rejected.json()["status"] == "issued" and rejected.json()["proof_rejected_reason"] == "المبلغ ناقص"

        # The customer asks to pay quarterly: a new request for the first of 4 installments.
        reissued = client.post(
            f"/insurance/requests/{request_id}/payment-request", json={"installments": 4}
        ).json()
        assert Decimal(reissued["amount_due"]) == Decimal("3000.00") and reissued["installments"] == 4
        client.post("/insurance/portal/track/payment/proof", files={"file": ("r2.pdf", PDF)}, headers=headers)
        paid = client.post(
            f"/insurance/payment-requests/{reissued['payment_id']}/confirm",
            json={"method": "bank_transfer", "bank_reference": "TRX-1"},
        ).json()
        assert paid["status"] == "paid" and paid["receipt_document_id"]
        receipt = client.get("/insurance/portal/track/payment/document?kind=receipt", headers=headers)
        assert "إيصال" in receipt.text and "3,000.00" in receipt.text
        assert (
            client.post(f"/insurance/payment-requests/{reissued['payment_id']}/confirm", json={}).status_code
            == 409
        )

        policy = client.post(f"/insurance/requests/{request_id}/issue-policy", json={"installments": 1})
        assert policy.status_code == 201
        detail = client.get(f"/insurance/portfolio/policies/{policy.json()['policy_id']}").json()
        assert detail["installments_total"] == 4 and detail["installments_paid"] == 1  # the plan of the payment
        first = detail["installments"][0]
        assert first["status"] == "paid" and first["payment_reference"] == reissued["payment_number"]

    statuses = sorted(p.status.value for p in (await db_session.execute(select(PaymentRequest))).scalars())
    assert statuses == ["cancelled", "paid"]
    kinds = await _kinds(db_session)
    for kind in ("payment_request", "payment_proof_rejected", "payment_received", "policy_issued"):
        assert kind in kinds


async def test_issue_on_credit_and_no_auto_request(db_session):
    with TestClient(app) as client:
        assert (
            client.put("/insurance/automation/rules/auto_payment_request", json={"enabled": False}).status_code
            == 200
        )
        created, _, headers = _accepted(client)
        assert client.get("/insurance/portal/track", headers=headers).json()["payment"] is None
        request_id = created["request_id"]
        client.post(
            f"/insurance/requests/{request_id}/payment-request", json={"installments": 1, "vat_amount": "1565.22"}
        )
        credit = client.post(f"/insurance/requests/{request_id}/issue-policy", json={"allow_unpaid": True})
        assert credit.status_code == 201


async def test_payment_reminders(db_session):
    with TestClient(app) as client:
        _accepted(client)
    p = (await db_session.execute(select(PaymentRequest))).scalar_one()
    due = p.due_date
    assert await jobs.payment_reminders(db_session, due - timedelta(days=5)) == 0
    assert await jobs.payment_reminders(db_session, due - timedelta(days=2)) == 1
    assert await jobs.payment_reminders(db_session, due - timedelta(days=1)) == 0  # once before the due date
    assert await jobs.payment_reminders(db_session, due + timedelta(days=1)) == 1
    assert await jobs.payment_reminders(db_session, due + timedelta(days=2)) == 0
    assert await jobs.payment_reminders(db_session, due + timedelta(days=3)) == 1
    await db_session.commit()
    assert (await _kinds(db_session)).count("payment_reminder") == 3
