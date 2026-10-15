"""The customer chooses an offer on the tracking page and accepts it electronically."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.automation.models import CustomerNotification
from app.insurance.models import OfferPresentation
from app.main import app
from tests.test_documents_review import DETAILS, _track_token

SIGN = {"signer_name": "سارة", "signer_title": "مديرة المشتريات", "id_number": "1012345678", "accept": True}


def _quoted_request(client: TestClient, offers: list[dict]) -> tuple[dict, list[dict]]:
    """A portal request out for pricing with one quote per entry of `offers`."""
    created = client.post(
        "/insurance/portal/requests",
        json={
            "company_name": "شركة الريادة",
            "cr_number": "1010123123",
            "full_name": "سارة",
            "mobile": "0551234567",
            "insurance_type": "motor",
            "product_type": "motor_tpl",
            "details": DETAILS,
            "consent": True,
        },
    ).json()
    insurers = [
        client.post("/insurance/insurers", json={"name": f"شركة {i}", "email": f"q{i}@ins.example"}).json()
        for i in range(len(offers))
    ]
    submissions = client.post(
        f"/insurance/requests/{created['request_id']}/submissions",
        json={"insurer_ids": [i["insurer_id"] for i in insurers]},
    ).json()
    by_insurer = {s["insurer_id"]: s for s in submissions}
    quotes = [
        client.post(
            f"/insurance/submissions/{by_insurer[insurer['insurer_id']]['submission_id']}/quotes", json=offer
        ).json()
        for insurer, offer in zip(insurers, offers, strict=True)
    ]
    return created, quotes


async def test_customer_compares_and_accepts(db_session):
    with TestClient(app) as client:
        created, quotes = _quoted_request(
            client,
            [
                {
                    "premium": "4200",
                    "coverage_limit": "10000000",
                    "commission_rate": "12",
                    "conditions": "تحمل 500",
                },
                {"premium": "3900", "commission_rate": "10"},
                {"premium": "5100"},
            ],
        )
        request_id = created["request_id"]
        assert client.get(f"/insurance/requests/{request_id}/offers").json() is None
        bad = client.post(f"/insurance/requests/{request_id}/offers", json={"quote_ids": ["nope"]})
        assert bad.status_code == 422
        sent = client.post(
            f"/insurance/requests/{request_id}/offers",
            json={
                "quote_ids": [quotes[0]["quote_id"], quotes[1]["quote_id"]],
                "recommended_quote_id": quotes[0]["quote_id"],
                "note": "ننصح بالعرض الأول لأن حد التغطية أعلى",
            },
        )
        assert sent.status_code == 201 and len(sent.json()["quotes"]) == 2

        headers = {"X-Track-Token": _track_token(client, created)}
        view = client.get("/insurance/portal/track", headers=headers).json()["offers"]
        assert view["status"] == "published" and view["recommended_quote_id"] == quotes[0]["quote_id"]
        assert [q["insurer"] for q in view["quotes"]] == ["شركة 0", "شركة 1"]
        assert all("commission" not in key for q in view["quotes"] for key in q)  # never shown to the customer

        assert (
            client.post(
                "/insurance/portal/track/offers/comment",
                json={"text": "هل يشمل المساعدة على الطريق؟"},
                headers=headers,
            ).status_code
            == 204
        )
        not_offered = client.post(
            "/insurance/portal/track/offers/accept",
            json={**SIGN, "quote_id": quotes[2]["quote_id"]},
            headers=headers,
        )
        assert not_offered.status_code == 422
        accepted = client.post(
            "/insurance/portal/track/offers/accept",
            json={**SIGN, "quote_id": quotes[0]["quote_id"]},
            headers=headers,
        )
        assert accepted.status_code == 200
        again = client.post(
            "/insurance/portal/track/offers/accept",
            json={**SIGN, "quote_id": quotes[1]["quote_id"]},
            headers=headers,
        )
        assert again.status_code == 409

        request = client.get(f"/insurance/requests/{request_id}").json()
        assert request["status"] == "quote_selected" and request["selected_quote_id"] == quotes[0]["quote_id"]
        staff_view = client.get(f"/insurance/requests/{request_id}/offers").json()
        assert staff_view["status"] == "accepted" and staff_view["signer_title"] == "مديرة المشتريات"
        assert staff_view["comments"][0]["text"] == "هل يشمل المساعدة على الطريق؟"
        text = staff_view["acceptance_text"]
        assert "نحن شركة الريادة، سجل تجاري رقم 1010123123، نقبل عرض شركة 0" in text and "4,200.00" in text
        assert "تحمل 500" in text
        assert client.get(f"/insurance/documents/{staff_view['document_id']}/download").status_code == 200

        verified = client.get(f"/insurance/portal/verify/{accepted.json()['verification_code']}").json()
        assert verified["kind"] == "acceptance" and verified["intact"] and "4,200.00" in verified["body_text"]
        resend = client.post(
            f"/insurance/requests/{request_id}/offers", json={"quote_ids": [quotes[1]["quote_id"]]}
        )
        assert resend.status_code == 409  # the request already has an accepted offer

    kinds = [n.kind for n in (await db_session.execute(select(CustomerNotification))).scalars()]
    assert kinds.count("offers_ready") == 1 and kinds.count("offer_accepted") == 1
    p = (await db_session.execute(select(OfferPresentation))).scalar_one()
    p.acceptance_text += " (معدّل)"
    await db_session.commit()
    with TestClient(app) as client:
        assert client.get(f"/insurance/portal/verify/{p.verification_code}").json()["intact"] is False


async def test_republishing_replaces_and_expired_offers_are_refused(db_session):
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with TestClient(app) as client:
        created, quotes = _quoted_request(
            client, [{"premium": "4000"}, {"premium": "4500", "validity_date": past}]
        )
        request_id = created["request_id"]
        expired = client.post(
            f"/insurance/requests/{request_id}/offers", json={"quote_ids": [quotes[1]["quote_id"]]}
        )
        assert expired.status_code == 409
        client.post(f"/insurance/requests/{request_id}/offers", json={"quote_ids": [quotes[0]["quote_id"]]})
        client.post(f"/insurance/requests/{request_id}/offers", json={"quote_ids": [quotes[0]["quote_id"]]})
        assert client.post(f"/insurance/requests/{request_id}/offers/withdraw").status_code == 200
        headers = {"X-Track-Token": _track_token(client, created)}
        assert client.get("/insurance/portal/track", headers=headers).json()["offers"] is None
        refused = client.post(
            "/insurance/portal/track/offers/accept",
            json={**SIGN, "quote_id": quotes[0]["quote_id"]},
            headers=headers,
        )
        assert refused.status_code == 409
    statuses = sorted(p.status.value for p in (await db_session.execute(select(OfferPresentation))).scalars())
    assert statuses == ["withdrawn", "withdrawn"]
