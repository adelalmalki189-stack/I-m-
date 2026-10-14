"""Phase 4b: insurer replies → matched to submissions → AI-extracted quotes that need review."""

from __future__ import annotations

from email.message import EmailMessage

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.automation import inbound
from app.automation.inbound import QuoteExtraction
from app.automation.models import InboundEmail
from app.insurance.models import InsuranceSubmission
from app.main import app

DETAILS = {
    "vehicle_make": "تويوتا",
    "vehicle_model": "كامري",
    "vehicle_year": 2022,
    "vehicle_count": 1,
    "vehicle_usage": "خاص",
}
PDF = b"%PDF-1.4 quote"


def _setup(client: TestClient) -> tuple[dict, list[dict]]:
    request = client.post(
        "/insurance/portal/requests",
        json={
            "full_name": "نورة القحطاني",
            "mobile": "0551234567",
            "insurance_type": "motor",
            "product_type": "motor_comprehensive",
            "details": DETAILS,
            "consent": True,
        },
    ).json()
    insurers = [
        client.post("/insurance/insurers", json={"name": "شركة أ", "email": "quotes@alpha-ins.example"}).json(),
        client.post("/insurance/insurers", json={"name": "شركة ب", "email": "rfq@beta-ins.example"}).json(),
    ]
    subs = client.post(
        f"/insurance/requests/{request['request_id']}/submissions",
        json={"insurer_ids": [i["insurer_id"] for i in insurers]},
    ).json()
    return request, subs


def _email(sender: str, subject: str, body: str, message_id: str, pdf: bool = True) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Message-ID"] = sender, "quotes@bud.example", subject, message_id
    msg.set_content(body)
    if pdf:
        msg.add_attachment(PDF, maintype="application", subtype="pdf", filename="offer.pdf")
    return msg.as_bytes()


def _extractor(result: QuoteExtraction, seen: list | None = None):
    async def fake(prompt: str, files: list[tuple[str, str, bytes]]) -> QuoteExtraction:
        if seen is not None:
            seen.append((prompt, files))
        return result

    return fake


QUOTE = QuoteExtraction(
    outcome="quote",
    premium=3450.0,
    currency="SAR",
    coverage_limit=90000.0,
    deductible="500 ر.س",
    exclusions="الاستخدام التجاري",
    conditions="فحص المركبة",
    validity_date="2026-11-30",
    commission_rate=12.5,
    insurer_reference="A-7781",
    questions_for_broker=None,
    confidence="high",
    notes=None,
)


async def test_reply_is_matched_extracted_and_needs_review(db_session):
    seen: list = []
    with TestClient(app) as client:
        request, subs = _setup(client)
        raw = _email(
            "Ahmed <ahmed@alpha-ins.example>",
            f"RE: طلب عرض سعر {request['request_number']}",
            "نرفق لكم عرضنا.",
            "<m1@alpha>",
        )
        counts = await inbound.process_mailbox(db_session, [raw, raw], _extractor(QUOTE, seen))
        assert counts == {"received": 1, "matched": 1, "quotes": 1, "declined": 0, "unmatched": 0, "failed": 0}
        prompt, files = seen[0]
        assert request["request_number"] in prompt and "<email>" in prompt
        assert files == [("offer.pdf", "application/pdf", PDF)]

        quotes = client.get(f"/insurance/requests/{request['request_id']}/quotes").json()
        assert len(quotes) == 1
        quote = quotes[0]
        assert quote["needs_review"] and quote["source_channel"] == "email_ai" and quote["premium"] == "3450.00"
        assert "A-7781" in quote["extraction_notes"]
        # Not usable until confirmed.
        assert client.get(f"/insurance/requests/{request['request_id']}/best-quote").status_code == 404
        select_url = f"/insurance/requests/{request['request_id']}/quotes/{quote['quote_id']}/select"
        assert client.post(select_url).json()["detail"]["error_code"] == "QUOTE_NEEDS_REVIEW"

        confirmed = client.post(f"/insurance/quotes/{quote['quote_id']}/confirm", json={"premium": "3400"})
        assert confirmed.json()["needs_review"] is False and confirmed.json()["premium"] == "3400"
        assert client.post(f"/insurance/quotes/{quote['quote_id']}/confirm", json={}).status_code == 409
        assert client.delete(f"/insurance/quotes/{quote['quote_id']}").status_code == 409
        assert client.post(select_url).status_code == 200

        listed = client.get(f"/insurance/inbound?request_id={request['request_id']}").json()
        assert listed[0]["status"] == "quote_created" and listed[0]["insurer_name"] == "شركة أ"
        assert listed[0]["attachments"][0]["name"] == "offer.pdf" and "path" not in listed[0]["attachments"][0]
        attachment = client.get(f"/insurance/inbound/{listed[0]['inbound_id']}/attachments/0")
        assert attachment.content == PDF


async def test_decline_marks_submission_rejected(db_session):
    with TestClient(app) as client:
        request, subs = _setup(client)
        raw = _email(
            "rfq@beta-ins.example", f"Re: {request['request_number']}", "نعتذر عن التسعير", "<m2@beta>", pdf=False
        )
        declined = QUOTE.model_copy(update={"outcome": "declined", "premium": None, "notes": "خارج نطاق الاكتتاب"})
        counts = await inbound.process_mailbox(db_session, [raw], _extractor(declined))
        assert counts["declined"] == 1
    beta_insurer = subs[1]["insurer_id"]
    rows = (await db_session.execute(select(InsuranceSubmission))).scalars().all()
    assert {r.insurer_id: r.status for r in rows}[beta_insurer] == "rejected"
    assert [r.status for r in rows if r.insurer_id != beta_insurer] == ["pending_approval"]


async def test_unmatched_email_can_be_linked_then_extracted(db_session, monkeypatch):
    with TestClient(app) as client:
        request, subs = _setup(client)
        # A broker's personal reply from an unknown domain, without the request number.
        raw = _email("someone@gmail.example", "عرض السعر", "العرض مرفق", "<m3@x>")
        counts = await inbound.process_mailbox(db_session, [raw], _extractor(QUOTE))
        assert counts["unmatched"] == 1
        row = client.get("/insurance/inbound?status=unmatched").json()[0]
        assert row["request_id"] is None
        assert client.post(f"/insurance/inbound/{row['inbound_id']}/extract").status_code == 409

        # With AI extraction switched off (the default) linking works but nothing is sent to the API.
        linked = client.post(
            f"/insurance/inbound/{row['inbound_id']}/link", json={"submission_id": subs[1]["submission_id"]}
        )
        assert linked.json()["status"] == "skipped" and linked.json()["request_id"] == request["request_id"]

        monkeypatch.setattr(inbound.settings, "ai_quote_extraction_enabled", True)
        monkeypatch.setattr(inbound, "claude_extractor", _extractor(QUOTE))
        retried = client.post(f"/insurance/inbound/{row['inbound_id']}/extract")
        assert retried.json()["status"] == "quote_created" and retried.json()["insurer_name"] == "شركة ب"
        assert client.post(f"/insurance/inbound/{row['inbound_id']}/extract").status_code == 409


async def test_extraction_failure_is_kept_for_retry(db_session):
    async def broken(prompt, files):
        raise RuntimeError("API unavailable")

    with TestClient(app) as client:
        request, _ = _setup(client)
        raw = _email("quotes@alpha-ins.example", request["request_number"], "عرض", "<m4@alpha>")
        counts = await inbound.process_mailbox(db_session, [raw], broken)
        assert counts["failed"] == 1
    row = (await db_session.execute(select(InboundEmail))).scalar_one()
    assert row.status == "failed" and "API unavailable" in row.error


async def test_ambiguous_domain_stays_unlinked_to_a_submission(db_session):
    with TestClient(app) as client:
        request, _ = _setup(client)
        # Same domain as no insurer, but has the request number: request known, submission not.
        raw = _email("x@unknown.example", f"بخصوص {request['request_number']}", "سؤال", "<m5@u>", pdf=False)
        counts = await inbound.process_mailbox(db_session, [raw], _extractor(QUOTE))
        assert counts["unmatched"] == 1
    row = (await db_session.execute(select(InboundEmail))).scalar_one()
    assert row.request_id == request["request_id"] and row.submission_id is None


def test_parse_email_handles_html_and_arabic():
    msg = EmailMessage()
    msg["From"] = "=?utf-8?b?2LTYsdmD2Kk=?= <A@Alpha-Ins.example>"
    msg["Subject"] = "عرض IR-20261014-ABCDEF12"
    msg["Message-ID"] = "<h1@alpha>"
    msg.set_content("<p>القسط <b>3,450</b> ريال</p>", subtype="html")
    parsed = inbound.parse_email(msg.as_bytes())
    assert parsed.from_address == "a@alpha-ins.example"
    assert "3,450" in parsed.text and "<b>" not in parsed.text
    assert inbound.REQUEST_NUMBER.search(parsed.subject)


async def test_claude_request_shape(monkeypatch):
    """The real extractor sends the right model, beta, fallback, schema and PDF — checked at the HTTP layer."""
    import json as jsonlib

    import anthropic
    import httpx2

    captured = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = jsonlib.loads(request.content)
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": QUOTE.model_dump_json()}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        })

    real = anthropic.AsyncAnthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda: real(
        api_key="test", http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))))
    result = await inbound.claude_extractor("prompt text", [("offer.pdf", "application/pdf", PDF)])

    assert result.premium == 3450.0 and result.commission_rate == 12.5
    body = captured["body"]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in captured["headers"]["anthropic-beta"]
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "premium" in body["output_config"]["format"]["schema"]["properties"]
    blocks = body["messages"][0]["content"]
    assert blocks[0]["type"] == "document" and blocks[0]["source"]["media_type"] == "application/pdf"
    assert blocks[-1] == {"type": "text", "text": "prompt text"}
    assert "third party" in body["system"]
