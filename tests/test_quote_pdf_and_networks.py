"""Quotation PDFs read into draft quotes, and insurers' medical networks from Excel."""

from __future__ import annotations

import io
from datetime import date

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.insurance import networks, quote_pdf
from app.main import app
from tests.test_documents_review import _track_token
from tests.test_offers import _quoted_request

ENGLISH = """Quotation No: Q-2026-7781
Total Premium (incl. VAT): SAR 48,300.00
VAT 15%: 6,300.00
Sum Insured: 2,500,000
Deductible: SAR 1,000 each and every claim
Valid until: 15/11/2026
Brokerage commission: 12.5%
Network: Class B"""


def text_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with a real text layer (Helvetica, ASCII)."""
    esc = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    stream = "BT /F1 11 Tf 50 800 Td 14 TL " + " ".join(f"({line}) '" for line in esc) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> "
        "/Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def test_rules_read_an_english_quotation():
    r = quote_pdf.extract_rules(ENGLISH, issued=date(2026, 10, 16))
    assert r.outcome == "quote" and r.premium == 48300 and r.coverage_limit == 2_500_000
    assert r.deductible == "SAR 1,000 each and every claim" and r.validity_date == "2026-11-15"
    assert r.commission_rate == 12.5 and r.insurer_reference == "Q-2026-7781" and r.confidence == "high"
    assert "6,300.00" in r.notes and quote_pdf.network_class_from(r) == "B"


def test_rules_read_arabic_including_reversed_lines():
    reversed_label = "إجمالي القسط:"[::-1]  # stored right-to-left (visual order), as some PDFs do
    text = "\n".join(
        [
            "رقم العرض: ع-445",
            f"٣٢٬٥٠٠ {reversed_label}",
            "حد التغطية: 1,000,000",
            "التحمل: 500 ريال لكل مطالبة",
            "العرض صالح لمدة 30 يوم",
            "الشبكة: الفئة A",
        ]
    )
    r = quote_pdf.extract_rules(text, issued=date(2026, 10, 16))
    assert r.premium == 32500 and r.coverage_limit == 1_000_000
    assert r.deductible == "500 ريال لكل مطالبة" and r.validity_date == "2026-11-15"
    assert quote_pdf.network_class_from(r) == "A"


def test_per_class_premiums_scans_and_declines():
    medical = "Total Premium: 410,000\nVIP 160,000\nClass A 140,000\nClass B 110,000"
    assert "VIP: 160,000.00" in quote_pdf.extract_rules(medical).notes
    empty = quote_pdf.extract_rules("   ")
    assert empty.outcome == "no_quote" and "ممسوحة" in empty.notes
    assert quote_pdf.extract_rules("We regret that we are unable to quote this risk.").outcome == "declined"
    assert quote_pdf.normalize_class("الفئة a") == "A" and quote_pdf.normalize_class("Class VIP") == "VIP"


def test_pdf_text_layer():
    text = quote_pdf.pdf_text(text_pdf(ENGLISH.splitlines()))
    assert "48,300.00" in text and "Total Premium" in text
    try:
        quote_pdf.pdf_text(b"%PDF-1.4 broken")
    except quote_pdf.PdfUnreadable:
        pass
    else:
        raise AssertionError("expected PdfUnreadable")


async def test_underwriter_uploads_quote_pdf(db_session, monkeypatch):
    sent: list = []

    async def fake_send(message):
        sent.append(message)

    monkeypatch.setattr("app.insurance.mailer.send", fake_send)
    monkeypatch.setattr("app.api.routes.insurance.settings.email_delivery_enabled", True)
    with TestClient(app) as client:
        created, _ = _quoted_request(client, [{"premium": "1"}, {"premium": "2"}])
        subs = client.get(f"/insurance/submissions?request_id={created['request_id']}").json()
        not_pdf = client.post(
            f"/insurance/submissions/{subs[0]['submission_id']}/quote-pdf", files={"file": ("q.txt", b"hello")}
        )
        assert not_pdf.status_code == 415
        response = client.post(
            f"/insurance/submissions/{subs[0]['submission_id']}/quote-pdf",
            files={"file": ("offer.pdf", text_pdf(ENGLISH.splitlines()))},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        quote = body["quote"]
        assert body["method"] == "rules" and quote["needs_review"] and quote["source_channel"] == "pdf"
        assert float(quote["premium"]) == 48300 and quote["network_class"] == "B"
        assert quote["deductible"].startswith("SAR 1,000") and quote["source_document_id"] == body["document_id"]
        assert "بالقواعد الآلية" in quote["extraction_notes"]
        assert client.get(f"/insurance/documents/{body['document_id']}/download").status_code == 200

        confirmed = client.post(
            f"/insurance/quotes/{quote['quote_id']}/confirm", json={"network_class": "class a"}
        )
        assert confirmed.json()["network_class"] == "A" and not confirmed.json()["needs_review"]

        # The other insurer's email never carries this insurer's quotation.
        other = subs[1]["submission_id"]
        client.post(f"/insurance/submissions/{other}/approve")
        assert client.post(f"/insurance/submissions/{other}/send").status_code == 200
    names = [part.get_filename() for part in sent[0].iter_attachments()]
    assert "offer.pdf" not in names


def network_xlsx(headers: list[str], rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_network_layouts():
    single = networks.parse(
        network_xlsx(
            ["اسم المستشفى", "المدينة", "النوع", "الفئة"],
            [
                ["مستشفى الحمادي", "الرياض", "Hospital", "VIP, A, B"],
                ["مجمع النخبة الطبي", "جدة", "Polyclinic", "B"],
                ["", "الرياض", "Hospital", "A"],
                ["صيدلية الدواء", "الدمام", "Pharmacy", ""],
            ],
        )
    )
    assert [r["classes"] for r in single.rows] == [["A", "B", "VIP"], ["B"]]
    assert single.rows[0]["provider_type"] == "مستشفى" and single.rows[1]["provider_type"] == "مجمع طبي / عيادة"
    assert len(single.issues) == 2  # blank name, no class
    ticks = networks.parse(
        network_xlsx(
            ["Provider Name", "City", "Type", "VIP", "A", "B"],
            [
                ["King Faisal Specialist", "Riyadh", "Hospital", "✓", "", ""],
                ["Dr Sulaiman", "Riyadh", "Hospital", "✓", "✓", "x"],
            ],
        )
    )
    assert ticks.class_columns == ["VIP", "A", "B"] and [r["classes"] for r in ticks.rows] == [
        ["VIP"],
        ["A", "B", "VIP"],
    ]
    default = networks.parse(
        network_xlsx(["Provider", "City"], [["Clinic One", "Jeddah"]]), default_class="Class C"
    )
    assert default.rows[0]["classes"] == ["C"]


async def test_network_upload_and_customer_search(db_session):
    with TestClient(app) as client:
        created, quotes = _quoted_request(
            client, [{"premium": "90000", "network_class": "A"}, {"premium": "80000"}]
        )
        subs = client.get(f"/insurance/submissions?request_id={created['request_id']}").json()
        insurer_id = next(s["insurer_id"] for s in subs if s["submission_id"] == quotes[0]["submission_id"])
        no_class = client.post(
            f"/insurance/insurers/{insurer_id}/network",
            files={"file": ("n.xlsx", network_xlsx(["Provider", "City"], [["X", "Riyadh"]]))},
        )
        assert no_class.status_code == 422
        uploaded = client.post(
            f"/insurance/insurers/{insurer_id}/network",
            files={
                "file": (
                    "network.xlsx",
                    network_xlsx(
                        ["اسم مقدم الخدمة", "المدينة", "النوع", "الفئة"],
                        [
                            ["مستشفى الحبيب", "الرياض", "مستشفى", "A, B"],
                            ["مستشفى المواساة", "الدمام", "مستشفى", "A"],
                            ["مستشفى السعودي الألماني", "جدة", "مستشفى", "VIP"],
                        ],
                    ),
                )
            },
        )
        assert (
            uploaded.status_code == 201 and uploaded.json()["providers"] == 3 and uploaded.json()["entries"] == 4
        )
        assert uploaded.json()["summary"]["by_class"] == {"A": 2, "B": 1, "VIP": 1}
        assert client.get(f"/insurance/insurers/{insurer_id}/network?network_class=A").json()["total"] == 2
        found = client.get(f"/insurance/insurers/{insurer_id}/network/providers?network_class=a&q=الحبيب").json()
        assert [p["name"] for p in found] == ["مستشفى الحبيب"]

        client.post(
            f"/insurance/requests/{created['request_id']}/offers",
            json={"quote_ids": [q["quote_id"] for q in quotes]},
        )
        headers = {"X-Track-Token": _track_token(client, created)}
        shown = client.get("/insurance/portal/track", headers=headers).json()["offers"]["quotes"]
        with_network = next(q for q in shown if q["quote_id"] == quotes[0]["quote_id"])
        assert with_network["network"]["total"] == 2 and with_network["network_class"] == "A"
        search = client.get(
            f"/insurance/portal/track/network?quote_id={quotes[0]['quote_id']}&q=المواساة", headers=headers
        ).json()
        assert [p["name"] for p in search["providers"]] == ["مستشفى المواساة"]
        missing = client.get(f"/insurance/portal/track/network?quote_id={quotes[1]['quote_id']}", headers=headers)
        assert missing.status_code == 404
