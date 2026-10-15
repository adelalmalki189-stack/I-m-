"""Phase 4: customer messages (outbox, sandbox/live delivery), reminders and scheduled jobs."""

from __future__ import annotations

import json
import smtplib
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.automation import jobs, service
from app.automation.models import CustomerNotification
from app.core.security import hash_password
from app.insurance.models import (
    InsurancePolicy,
    InsuranceRequest,
    InsuranceSubmission,
    PremiumInstallment,
    SalesEmployee,
)
from app.main import app

DETAILS = {
    "vehicle_make": "تويوتا",
    "vehicle_model": "كامري",
    "vehicle_year": 2022,
    "vehicle_count": 1,
    "vehicle_usage": "خاص",
}


def _request(client: TestClient, mobile: str = "0551234567") -> dict:
    return client.post(
        "/insurance/portal/requests",
        json={
            "full_name": "نورة القحطاني",
            "mobile": mobile,
            "company_name": "شركة اختبار",
            "cr_number": "10" + str(mobile)[-8:],
            "insurance_type": "motor",
            "product_type": "motor_comprehensive",
            "details": DETAILS,
            "consent": True,
        },
    ).json()


def _issue(
    client: TestClient, start: date, installments: int = 1, mobile: str = "0551234567"
) -> tuple[dict, dict]:
    request = _request(client, mobile)
    insurer = client.post(
        "/insurance/insurers", json={"name": f"شركة {mobile}", "email": f"q{mobile}@x.example"}
    ).json()
    sub = client.post(
        f"/insurance/requests/{request['request_id']}/submissions", json={"insurer_ids": [insurer["insurer_id"]]}
    ).json()[0]
    quote = client.post(f"/insurance/submissions/{sub['submission_id']}/quotes", json={"premium": "2400"}).json()
    client.post(f"/insurance/requests/{request['request_id']}/quotes/{quote['quote_id']}/select")
    policy = client.post(
        f"/insurance/requests/{request['request_id']}/issue-policy",
        json={"installments": installments, "effective_from": f"{start.isoformat()}T00:00:00Z"},
    ).json()
    return policy, request


async def _outbox(db_session, kind: str | None = None) -> list[CustomerNotification]:
    db_session.expire_all()
    stmt = select(CustomerNotification).order_by(CustomerNotification.created_at)
    if kind:
        stmt = stmt.where(CustomerNotification.kind == kind)
    return list((await db_session.execute(stmt)).scalars())


async def test_events_queue_messages_and_sandbox_simulates(db_session):
    with TestClient(app) as client:
        request = _request(client)
        client.patch(f"/insurance/requests/{request['request_id']}/status", json={"status": "needs_info"})
    received = await _outbox(db_session, "request_received")
    assert len(received) == 1 and request["request_number"] in received[0].body
    assert received[0].to_phone == "+966551234567" and received[0].status == "queued"
    assert len(await _outbox(db_session, "missing_info")) == 1

    counts = await service.dispatch_outbox(db_session)
    assert counts["simulated"] == 2
    assert {n.status for n in await _outbox(db_session)} == {"simulated"}


async def test_policy_issue_and_claim_updates_notify(db_session):
    with TestClient(app) as client:
        policy, _ = _issue(client, date.today() - timedelta(days=10))
        claim = client.post(
            "/insurance/claims",
            json={
                "policy_id": policy["policy_id"],
                "incident_date": date.today().isoformat(),
                "description": "صدمة خلفية",
            },
        ).json()
        url = f"/insurance/claims/{claim['claim_id']}/status"
        client.post(url, json={"status": "submitted"})
        client.post(url, json={"status": "approved", "approved_amount": "1500"})
    issued = await _outbox(db_session, "policy_issued")
    assert len(issued) == 1 and policy["policy_number"] in issued[0].body and "2,400.00 ر.س" in issued[0].body
    updates = [n.body for n in await _outbox(db_session, "claim_update")]
    assert len(updates) == 2 and "1,500.00" in updates[1]


async def test_opt_out_and_disabled_rule(db_session):
    with TestClient(app) as client:
        first = _request(client)
        customer_id = client.get(f"/insurance/requests/{first['request_id']}").json()["customer_id"]
        client.patch(f"/insurance/customers/{customer_id}", json={"messaging_opt_out": True})
        client.patch(f"/insurance/requests/{first['request_id']}/status", json={"status": "needs_info"})
        assert (
            client.put("/insurance/automation/rules/request_received", json={"enabled": False}).status_code == 200
        )
        _request(client, "0559990000")
    skipped = await _outbox(db_session, "missing_info")
    assert skipped[0].status == "skipped" and "ألغى" in skipped[0].last_error
    assert len(await _outbox(db_session, "request_received")) == 1  # only the one before the rule was disabled


async def test_rule_editing_validation(db_session):
    with TestClient(app) as client:
        bad = client.put("/insurance/automation/rules/renewal_reminder", json={"message": "مرحبًا {nope}"})
        assert bad.status_code == 422
        assert (
            client.put(
                "/insurance/automation/rules/renewal_reminder", json={"params": {"days": [500]}}
            ).status_code
            == 422
        )
        assert (
            client.put("/insurance/automation/rules/renewal_reminder", json={"params": {"weeks": 2}}).status_code
            == 422
        )
        assert client.put("/insurance/automation/rules/expire_policies", json={"message": "x"}).status_code == 422
        ok = client.put(
            "/insurance/automation/rules/renewal_reminder",
            json={"message": "تجديد {policy_number}", "params": {"days": [14, 45]}},
        )
        rule = next(r for r in ok.json()["rules"] if r["key"] == "renewal_reminder")
        assert rule["message"] == "تجديد {policy_number}" and rule["params"]["days"] == [45, 14]
        reset = client.put("/insurance/automation/rules/renewal_reminder", json={"message": ""}).json()
        assert (
            next(r for r in reset["rules"] if r["key"] == "renewal_reminder")["message"]
            == next(r for r in reset["rules"] if r["key"] == "renewal_reminder")["default_message"]
        )
        live = client.put("/insurance/automation/settings", json={"messaging_mode": "live"})
        assert live.status_code == 409 and live.json()["detail"]["error_code"] == "CHANNEL_NOT_CONFIGURED"


async def test_renewal_reminders_pick_one_threshold_and_never_repeat(db_session):
    with TestClient(app) as client:
        # Issued 345 days ago → ends in ~20 days: only the 30-day reminder applies.
        policy, _ = _issue(client, date.today() - timedelta(days=345))
    today = date.today()
    assert await jobs.renewal_reminders(db_session, today) == 1
    assert await jobs.renewal_reminders(db_session, today) == 0
    await db_session.commit()
    reminders = await _outbox(db_session, "renewal_reminder")
    assert [n.dedupe_key for n in reminders] == [f"renewal:{policy['policy_id']}:30"]
    assert await jobs.renewal_reminders(db_session, today + timedelta(days=14)) == 1  # now ≤ 7 days
    await db_session.commit()
    assert len(await _outbox(db_session, "renewal_reminder")) == 2


async def test_auto_renewal_and_expiry(db_session):
    with TestClient(app) as client:
        soon, _ = _issue(client, date.today() - timedelta(days=340))  # ~25 days left
        later, _ = _issue(client, date.today() - timedelta(days=100), mobile="0552223333")
        old, _ = _issue(client, date.today() - timedelta(days=400), mobile="0553334444")  # already ended
    summary = await jobs.run_daily_jobs(db_session)
    assert summary["expired"] == 1 and summary["auto_renewals"] == 1
    assert (await jobs.run_daily_jobs(db_session))["auto_renewals"] == 0  # idempotent
    renewals = (
        (await db_session.execute(select(InsuranceRequest).where(InsuranceRequest.request_source == "renewal")))
        .scalars()
        .all()
    )
    assert [r.renewal_of_policy_id for r in renewals] == [soon["policy_id"]]
    db_session.expire_all()
    assert (await db_session.get(InsurancePolicy, old["policy_id"])).status == "expired"
    assert (await db_session.get(InsurancePolicy, later["policy_id"])).status == "issued"


async def test_installment_due_and_overdue_reminders(db_session):
    with TestClient(app) as client:
        policy, _ = _issue(client, date.today() - timedelta(days=40), installments=12)
    items = (
        (
            await db_session.execute(
                select(PremiumInstallment)
                .where(PremiumInstallment.policy_id == policy["policy_id"])
                .order_by(PremiumInstallment.sequence)
            )
        )
        .scalars()
        .all()
    )
    items[2].due_date = date.today() + timedelta(days=2)  # upcoming
    await db_session.commit()
    counts = await jobs.installment_reminders(db_session, date.today())
    # #1 is ~40 days late (7-day step), #2 ~10 days late (7-day step), #3 due in 2 days
    assert counts == {"due": 1, "overdue": 2}
    assert await jobs.installment_reminders(db_session, date.today()) == {"due": 0, "overdue": 0}


async def test_insurer_followups(db_session, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def send_message(self, message):
            sent.append(message)

    with TestClient(app) as client:
        request = _request(client)
        insurer = client.post("/insurance/insurers", json={"name": "شركة بطيئة", "email": "slow@x.example"}).json()
        sub = client.post(
            f"/insurance/requests/{request['request_id']}/submissions",
            json={"insurer_ids": [insurer["insurer_id"]]},
        ).json()[0]
        client.post(f"/insurance/submissions/{sub['submission_id']}/approve")
        client.post(f"/insurance/submissions/{sub['submission_id']}/mark-sent", json={})
    submission = await db_session.get(InsuranceSubmission, sub["submission_id"])
    submission.sent_at = datetime.now(UTC) - timedelta(hours=50)
    await db_session.commit()

    now = datetime.now(UTC)
    assert await jobs.insurer_followups(db_session, now) == {"emailed": 0, "flagged": 1}  # email disabled
    await db_session.commit()
    assert (await db_session.get(InsuranceSubmission, sub["submission_id"])).status == "follow_up_required"

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr("app.automation.jobs.settings.email_delivery_enabled", True)
    for hours in (49, 98, 147):  # each run 49h after the previous contact
        await jobs.insurer_followups(db_session, now + timedelta(hours=hours))
        await db_session.commit()
    assert len(sent) == 2  # max_followups
    assert sent[0]["To"] == "slow@x.example" and sent[0]["Subject"].startswith("متابعة:")


def _live(monkeypatch, handler):
    monkeypatch.setattr("app.automation.channels.settings.whatsapp_token", "token")
    monkeypatch.setattr("app.automation.channels.settings.whatsapp_phone_number_id", "123")
    monkeypatch.setattr("app.automation.channels.settings.unifonic_app_sid", "sid")
    monkeypatch.setattr("app.automation.channels.settings.unifonic_sender_id", "BUD")
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_live_whatsapp_template_and_retries(db_session, monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 2:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{len(calls)}"}]})

    http = _live(monkeypatch, handler)
    with TestClient(app) as client:
        assert (
            client.put(
                "/insurance/automation/settings", json={"messaging_mode": "live", "customer_channel": "whatsapp"}
            ).status_code
            == 200
        )
        client.put(
            "/insurance/automation/rules/request_received", json={"whatsapp_template": "bud_request_received"}
        )
        first = _request(client)
        _request(client, "0557778888")
    now = datetime.now(UTC)
    counts = await service.dispatch_outbox(db_session, http, now)
    assert counts == {"sent": 1, "simulated": 0, "retry": 1, "failed": 0, "skipped": 0}
    payload = calls[0]
    assert payload["to"] == "966551234567" and payload["type"] == "template"
    assert payload["template"]["name"] == "bud_request_received"
    params = [p["text"] for p in payload["template"]["components"][0]["parameters"]]
    assert params == ["شركة اختبار", first["request_number"], "المركبات"]
    assert (await service.dispatch_outbox(db_session, http, now))["retry"] == 0  # backoff not elapsed
    assert (await service.dispatch_outbox(db_session, http, now + timedelta(minutes=2)))["sent"] == 1
    assert {n.provider_message_id for n in await _outbox(db_session)} == {"wamid.1", "wamid.3"}


async def test_live_sms_rejection_is_not_retried_and_can_be_resent(db_session, monkeypatch):
    http = _live(
        monkeypatch, lambda request: httpx.Response(400, json={"success": False, "message": "Invalid recipient"})
    )
    with TestClient(app) as client:
        client.put("/insurance/automation/settings", json={"messaging_mode": "live", "customer_channel": "sms"})
        request = _request(client)
        customer_id = client.get(f"/insurance/requests/{request['request_id']}").json()["customer_id"]
        manual = client.post(f"/insurance/customers/{customer_id}/messages", json={"text": "نرجو التواصل معنا"})
        assert manual.status_code == 201 and manual.json()["kind_label"] == "رسالة يدوية"
    counts = await service.dispatch_outbox(db_session, http)
    assert counts["failed"] == 2
    with TestClient(app) as client:
        failed = client.get("/insurance/notifications?status=failed").json()
        assert len(failed) == 2 and "HTTP 400" in failed[0]["last_error"]
        retried = client.post(f"/insurance/notifications/{failed[0]['notification_id']}/retry")
        assert retried.json()["status"] == "queued"
        assert client.post(f"/insurance/notifications/{failed[0]['notification_id']}/retry").status_code == 409


@pytest.mark.real_auth
async def test_only_managers_configure_automation(db_session):
    db_session.add(
        SalesEmployee(
            full_name="uw",
            email="uw@bud.example",
            role="underwriter",
            password_hash=hash_password("Strong-pass-2026"),
        )
    )
    await db_session.commit()
    with TestClient(app) as client:
        client.post("/auth/login", json={"email": "uw@bud.example", "password": "Strong-pass-2026"})
        assert client.get("/insurance/automation").status_code == 403
        assert client.post("/insurance/automation/run").status_code == 403
        assert client.get("/insurance/notifications").status_code == 200
