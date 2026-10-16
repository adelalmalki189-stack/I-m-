"""Offers shown to the customer, and their electronic acceptance.

Staff publish a selection of confirmed quotes (optionally recommending one); the customer
sees them on the tracking page — never the broker commission — and accepts one after the
mobile one-time code. The acceptance selects the quote on the request (status
quote_selected) and is frozen and verifiable like the authorization letter.
"""

from __future__ import annotations

import hashlib
import html
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.insurance import aftersales, authorization, catalog, documents, networks, workflow
from app.insurance.models import (
    Customer,
    InsuranceQuote,
    InsuranceRequest,
    InsuranceRequestStatus,
    InsuranceSubmission,
    Insurer,
    OfferPresentation,
    OfferPresentationStatus,
)

ACCEPTANCE_DOCUMENT = "offer_acceptance"


class OfferError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def expired(quote: InsuranceQuote, now: datetime | None = None) -> bool:
    return quote.validity_date is not None and _utc(quote.validity_date) < (now or datetime.now(UTC))


async def request_quotes(session: AsyncSession, request_id: str) -> list[tuple[InsuranceQuote, Insurer | None]]:
    """Confirmed quotes of a request with their insurer (AI drafts awaiting review excluded)."""
    rows = await session.execute(
        select(InsuranceQuote, Insurer)
        .join(InsuranceSubmission, InsuranceSubmission.submission_id == InsuranceQuote.submission_id)
        .outerjoin(Insurer, Insurer.insurer_id == InsuranceSubmission.insurer_id)
        .where(InsuranceSubmission.request_id == request_id, InsuranceQuote.needs_review.is_(False))
    )
    return [(q, i) for q, i in rows.all()]


def quote_view(
    quote: InsuranceQuote, insurer: Insurer | None, network: dict[str, Any] | None = None
) -> dict[str, Any]:
    """What the customer may see of a quote: no commission, no internal notes."""
    return {
        "deductible": quote.deductible,
        "network_class": quote.network_class,
        "network": network,
        "quote_id": quote.quote_id,
        "insurer": insurer.name if insurer else "شركة تأمين",
        "premium": aftersales.to_money(quote.premium),
        "currency": quote.currency or "SAR",
        "coverage_limit": aftersales.to_money(quote.coverage_limit),
        "conditions": quote.conditions,
        "exclusions": quote.exclusions,
        "validity_date": quote.validity_date,
        "expired": expired(quote),
    }


async def current(session: AsyncSession, request_id: str) -> OfferPresentation | None:
    return (
        (
            await session.execute(
                select(OfferPresentation)
                .where(
                    OfferPresentation.request_id == request_id,
                    OfferPresentation.status != OfferPresentationStatus.WITHDRAWN,
                )
                .order_by(OfferPresentation.created_at.desc())
            )
        )
        .scalars()
        .first()
    )


def comments(p: OfferPresentation) -> list[dict[str, str]]:
    try:
        return list(json.loads(p.customer_comments or "[]"))
    except json.JSONDecodeError:
        return []


async def view(session: AsyncSession, p: OfferPresentation | None, staff: bool = False) -> dict[str, Any] | None:
    if p is None:
        return None
    by_id = {q.quote_id: (q, i) for q, i in await request_quotes(session, p.request_id)}
    shown = []
    for qid in json.loads(p.quote_ids):
        if qid not in by_id:
            continue
        quote, insurer = by_id[qid]
        network = None
        if quote.network_class and insurer is not None:
            s = await networks.summary(session, insurer.insurer_id, quote.network_class)
            network = (
                {"total": s["total"], "by_type": s["by_type"], "top_cities": s["top_cities"]}
                if s["total"]
                else None
            )
        shown.append(quote_view(quote, insurer, network))
    accepted = p.status == OfferPresentationStatus.ACCEPTED
    out: dict[str, Any] = {
        "presentation_id": p.presentation_id,
        "status": p.status.value,
        "published_at": p.created_at,
        "note": p.note_to_customer,
        "recommended_quote_id": p.recommended_quote_id,
        "quotes": shown,
        "accepted_quote_id": p.accepted_quote_id,
        "signed_at": p.signed_at,
        "signer_name": p.signer_name,
        "verification_code": p.verification_code if accepted else None,
        "comments": comments(p),
    }
    if staff and accepted:
        out |= {
            "signer_title": p.signer_title,
            "signer_id_last4": p.signer_id_last4,
            "signed_mobile": authorization.mask_mobile(p.signed_mobile),
            "content_hash": p.content_hash,
            "document_id": p.document_id,
            "acceptance_text": p.acceptance_text,
        }
    return out


async def publish(
    session: AsyncSession,
    request: InsuranceRequest,
    quote_ids: list[str],
    recommended_quote_id: str | None,
    note: str | None,
    staff_id: str | None,
) -> OfferPresentation:
    if request.status not in (InsuranceRequestStatus.READY_FOR_QUOTE, InsuranceRequestStatus.UNDER_REVIEW):
        raise OfferError(409, "OFFERS_NOT_OPEN", "Offers can be sent while the request awaits a choice")
    available = {q.quote_id: q for q, _ in await request_quotes(session, request.request_id)}
    chosen = list(dict.fromkeys(quote_ids))
    if not chosen or any(qid not in available for qid in chosen):
        raise OfferError(422, "QUOTE_NOT_FOR_REQUEST", "Choose confirmed quotes of this request")
    if any(expired(available[qid]) for qid in chosen):
        raise OfferError(409, "QUOTE_EXPIRED", "An offer's validity has passed; ask the insurer to renew it")
    if recommended_quote_id and recommended_quote_id not in chosen:
        raise OfferError(422, "QUOTE_NOT_FOR_REQUEST", "The recommended offer must be one of those sent")
    previous = await current(session, request.request_id)
    if previous is not None:
        if previous.status == OfferPresentationStatus.ACCEPTED:
            raise OfferError(409, "OFFER_ALREADY_ACCEPTED", "The customer already accepted an offer")
        previous.status = OfferPresentationStatus.WITHDRAWN
    p = OfferPresentation(
        request_id=request.request_id,
        status=OfferPresentationStatus.PUBLISHED,
        quote_ids=json.dumps(chosen),
        recommended_quote_id=recommended_quote_id,
        note_to_customer=(note or "").strip() or None,
        published_by_id=staff_id,
        verification_code=authorization.new_verification_code(),
    )
    session.add(p)
    await session.flush()
    return p


def acceptance_text(
    p: OfferPresentation, request: InsuranceRequest, customer: Customer, quote: InsuranceQuote, insurer: str
) -> str:
    company = customer.company_name or customer.full_name
    cr = f"، سجل تجاري رقم {customer.cr_number}" if customer.cr_number else ""
    premium = aftersales.to_money(quote.premium)
    cover = aftersales.to_money(quote.coverage_limit)
    currency = {"SAR": "ر.س", "": "ر.س"}.get(quote.currency or "", quote.currency)
    lines = [
        f"قبول عرض تأمين - طلب رقم {request.request_number}",
        f"التاريخ: {authorization.ar_date(datetime.now(UTC).date())}",
        "",
        f"نحن {company}{cr}، نقبل عرض {insurer} لتأمين "
        f"{catalog.line_label(request.insurance_type)} - "
        f"{catalog.product_label(request.insurance_type, request.product_type)}",
        f"بقسط {premium:,.2f} {currency}" if premium is not None else "بالقسط الوارد في العرض",
    ]
    if cover is not None:
        lines.append(f"وحد تغطية {cover:,.2f} {currency}")
    if quote.conditions:
        lines.append(f"وبالشروط والتحمل: {quote.conditions}")
    if quote.exclusions:
        lines.append(f"ومع الاستثناءات: {quote.exclusions}")
    lines += [
        "",
        f"ونفوّض {settings.broker_legal_name} بإبلاغ شركة التأمين بقبولنا واستكمال إصدار الوثيقة،",
        "ونعلم أن الوثيقة تصدر وفق شروط الشركة بعد سداد القسط أو الدفعة الأولى.",
        "ويقرّ الموقّع بأنه مخوّل بالتوقيع عن المنشأة.",
    ]
    return "\n".join(lines)


def signature_hash(p: OfferPresentation) -> str:
    record = {
        "presentation_id": p.presentation_id,
        "accepted_quote_id": p.accepted_quote_id,
        "acceptance_text": p.acceptance_text,
        "signer_name": p.signer_name,
        "signer_id_last4": p.signer_id_last4,
        "signer_title": p.signer_title,
        "signed_mobile": p.signed_mobile,
        "signed_at": authorization.utc_seconds(p.signed_at),
    }
    return hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def verify_url(p: OfferPresentation) -> str:
    return f"{settings.public_base_url}/dashboard/verify.html?code={p.verification_code}"


def _signed_html(p: OfferPresentation) -> str:
    signed_at = authorization.utc_seconds(p.signed_at) or ""
    body = "".join(f"<p>{html.escape(line) or '&nbsp;'}</p>" for line in (p.acceptance_text or "").split("\n"))
    rows = [
        ("الموقّع", p.signer_name or ""),
        ("صفته في المنشأة", p.signer_title or ""),
        ("آخر 4 أرقام من الهوية", f"••••••{p.signer_id_last4}"),
        ("الجوال المتحقَّق منه برمز لمرة واحدة", authorization.mask_mobile(p.signed_mobile)),
        ("وقت التوقيع", signed_at),
        ("رمز التحقق", p.verification_code),
        ("بصمة التوقيع (SHA-256)", p.content_hash or ""),
    ]
    table = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>" for k, v in rows)
    url = html.escape(verify_url(p))
    return f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<title>قبول عرض {html.escape(p.verification_code)}</title>
<style>body{{font-family:Tahoma,sans-serif;max-width:720px;margin:32px auto;padding:0 16px;
line-height:1.9;color:#111}}p{{margin:0}}
.sig{{margin-top:28px;padding:14px;border:1px solid #999;border-radius:6px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:6px;border-bottom:1px solid #ddd;text-align:right}}
th{{width:42%;color:#555;font-weight:normal}}td{{word-break:break-all}}.verify{{margin-top:10px;font-size:12px}}</style>
</head><body>{body}
<div class="sig"><b>توقيع إلكتروني</b><table>{table}</table>
<div class="verify">للتحقق من صحة هذا القبول: <a href="{url}">{url}</a></div></div>
</body></html>"""


async def accept(
    session: AsyncSession,
    request: InsuranceRequest,
    customer: Customer,
    quote_id: str,
    signer_name: str,
    id_number: str,
    signer_title: str,
    mobile: str,
    ip: str,
    user_agent: str,
) -> OfferPresentation:
    p = await current(session, request.request_id)
    if p is None or p.status != OfferPresentationStatus.PUBLISHED:
        raise OfferError(409, "NO_OFFERS_TO_ACCEPT", "There are no offers awaiting your choice")
    if quote_id not in json.loads(p.quote_ids):
        raise OfferError(422, "QUOTE_NOT_OFFERED", "This offer is not one of those sent to you")
    found = {q.quote_id: (q, i) for q, i in await request_quotes(session, request.request_id)}
    if quote_id not in found:
        raise OfferError(409, "QUOTE_NOT_OFFERED", "This offer is no longer available")
    quote, insurer = found[quote_id]
    if expired(quote):
        raise OfferError(409, "QUOTE_EXPIRED", "This offer has expired; ask BUD to renew it")

    p.accepted_quote_id = quote_id
    p.acceptance_text = acceptance_text(p, request, customer, quote, insurer.name if insurer else "شركة التأمين")
    p.signer_name, p.signer_title = signer_name.strip(), signer_title.strip()
    p.signer_id_last4 = id_number[-4:]
    p.signed_mobile, p.signed_ip, p.signed_user_agent = mobile, ip, user_agent[:255]
    p.signed_at = datetime.now(UTC)
    p.content_hash = signature_hash(p)
    p.status = OfferPresentationStatus.ACCEPTED

    # A staff member may have picked a quote meanwhile; the customer's signed choice wins.
    if request.status == InsuranceRequestStatus.UNDER_REVIEW:
        workflow.transition(request, InsuranceRequestStatus.READY_FOR_QUOTE)
    if request.status == InsuranceRequestStatus.READY_FOR_QUOTE:
        workflow.transition(request, InsuranceRequestStatus.QUOTE_SELECTED)
    elif request.status != InsuranceRequestStatus.QUOTE_SELECTED:
        raise OfferError(409, "REQUEST_NOT_OPEN", "This request is no longer open")
    request.selected_quote_id = quote_id

    copy = await documents.store_bytes(
        session,
        request.request_id,
        _signed_html(p).encode(),
        f"قبول-عرض-{request.request_number}.html",
        "text/html",
        ACCEPTANCE_DOCUMENT,
        reviewed=False,
    )
    copy.review_status, copy.is_verified, copy.reviewed_at = "accepted", True, p.signed_at
    p.document_id = copy.document_id
    await session.flush()
    return p


def add_comment(p: OfferPresentation, text: str) -> None:
    items = comments(p)
    items.append({"at": datetime.now(UTC).isoformat(timespec="seconds"), "text": text.strip()})
    p.customer_comments = json.dumps(items[-20:], ensure_ascii=False)
