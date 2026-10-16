"""Payment requests between the customer's acceptance and the policy issue.

BUD asks for the first payment of the chosen plan (the whole premium when paid at once),
with its bank details and a reference to quote on the transfer. The customer uploads the
transfer receipt on the tracking page; staff confirm the money arrived (or refuse the proof
with a reason). Issuing the policy then records that first installment as paid.

This is a payment request from the broker, not a ZATCA tax invoice: the insurer invoices
the premium.
"""

from __future__ import annotations

import html
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.insurance import aftersales, catalog, documents
from app.insurance.models import (
    Customer,
    InsuranceQuote,
    InsuranceRequest,
    InsuranceSubmission,
    Insurer,
    PaymentRequest,
    PaymentRequestStatus,
)

NOTICE, PROOF, RECEIPT = "payment_notice", "payment_proof", "payment_receipt"
METHODS = {"bank_transfer": "تحويل بنكي", "online": "دفع إلكتروني", "cheque": "شيك", "cash": "نقدًا"}
CENT = Decimal("0.01")
OPEN = (PaymentRequestStatus.ISSUED, PaymentRequestStatus.PROOF_SUBMITTED)


class PaymentError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def first_installment(total: Decimal, count: int) -> Decimal:
    """Same split as the policy's installments: equal parts, rounding left on the last one."""
    return total if count == 1 else (total / count).quantize(CENT, rounding=ROUND_HALF_UP)


async def latest(session: AsyncSession, request_id: str) -> PaymentRequest | None:
    return (
        (
            await session.execute(
                select(PaymentRequest)
                .where(
                    PaymentRequest.request_id == request_id,
                    PaymentRequest.status != PaymentRequestStatus.CANCELLED,
                )
                .order_by(PaymentRequest.created_at.desc())
            )
        )
        .scalars()
        .first()
    )


def payment_link(p: PaymentRequest) -> str | None:
    template = settings.payment_link_template
    return template.format(reference=p.payment_number, amount=f"{p.amount_due:.2f}") if template else None


def money(value: Decimal | None) -> str:
    return f"{value:,.2f} ر.س" if value is not None else "—"


def view(p: PaymentRequest | None, staff: bool = False) -> dict[str, Any] | None:
    if p is None:
        return None
    out: dict[str, Any] = {
        "payment_id": p.payment_id,
        "payment_number": p.payment_number,
        "status": p.status.value,
        "total_premium": p.total_premium,
        "vat_amount": p.vat_amount,
        "installments": p.installments,
        "amount_due": p.amount_due,
        "currency": p.currency,
        "due_date": p.due_date,
        "overdue": p.status in OPEN and p.due_date < datetime.now(UTC).date(),
        "bank": {"bank_name": p.bank_name, "account_name": p.account_name, "iban": p.iban} if p.iban else None,
        "payment_link": payment_link(p),
        "proof_submitted_at": p.proof_submitted_at,
        "proof_rejected_reason": p.proof_rejected_reason,
        "paid_at": p.paid_at,
        "payment_method": METHODS.get(p.payment_method or "", p.payment_method),
    }
    if staff:
        out |= {
            "document_id": p.document_id,
            "proof_document_id": p.proof_document_id,
            "receipt_document_id": p.receipt_document_id,
            "bank_reference": p.bank_reference,
        }
    return out


async def _quote(session: AsyncSession, request: InsuranceRequest) -> tuple[InsuranceQuote, Insurer | None]:
    quote = await session.get(InsuranceQuote, request.selected_quote_id) if request.selected_quote_id else None
    if quote is None:
        raise PaymentError(409, "NO_SELECTED_QUOTE", "Select (or let the customer accept) an offer first")
    submission = await session.get(InsuranceSubmission, quote.submission_id)
    insurer = await session.get(Insurer, submission.insurer_id) if submission else None
    return quote, insurer


def _page(title: str, rows: list[tuple[str, str]], footer: str) -> bytes:
    table = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>" for k, v in rows)
    return f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>body{{font-family:Tahoma,sans-serif;max-width:720px;margin:32px auto;padding:0 16px;
line-height:1.8;color:#111}}
h1{{font-size:20px}}table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{padding:8px;border-bottom:1px solid #ddd;text-align:right}}th{{width:40%;color:#555;font-weight:normal}}
.foot{{margin-top:18px;font-size:12px;color:#555}}</style></head><body>
<h1>{html.escape(title)}</h1><p>{html.escape(settings.broker_legal_name)}</p><table>{table}</table>
<p class="foot">{html.escape(footer)}</p></body></html>""".encode()


async def issue(
    session: AsyncSession,
    request: InsuranceRequest,
    customer: Customer,
    installments: int,
    staff_id: str | None,
    vat_amount: Decimal | None = None,
    due_days: int | None = None,
) -> PaymentRequest:
    """Ask for the first payment; an earlier unpaid request of the request is cancelled."""
    if installments not in aftersales.ALLOWED_INSTALLMENTS:
        raise PaymentError(
            422, "INVALID_INSTALLMENTS", f"installments must be one of {aftersales.ALLOWED_INSTALLMENTS}"
        )
    quote, insurer = await _quote(session, request)
    total = aftersales.to_money(quote.premium)
    if total is None or total <= 0:
        raise PaymentError(422, "INVALID_PREMIUM", "The selected offer has no valid premium")
    if vat_amount is not None and not 0 <= vat_amount < total:
        raise PaymentError(422, "INVALID_VAT", "VAT must be part of the premium")
    previous = await latest(session, request.request_id)
    if previous is not None:
        if previous.status == PaymentRequestStatus.PAID:
            raise PaymentError(409, "ALREADY_PAID", "This request is already paid")
        previous.status = PaymentRequestStatus.CANCELLED

    today = datetime.now(UTC).date()
    p = PaymentRequest(
        payment_number=f"PAY-{today.strftime('%y%m%d')}-{secrets.token_hex(3).upper()}",
        request_id=request.request_id,
        quote_id=quote.quote_id,
        status=PaymentRequestStatus.ISSUED,
        total_premium=total,
        vat_amount=vat_amount,
        installments=installments,
        amount_due=first_installment(total, installments),
        currency=quote.currency or "SAR",
        due_date=today + timedelta(days=due_days if due_days is not None else settings.payment_due_days),
        bank_name=settings.payment_bank_name or None,
        account_name=settings.payment_account_name or None,
        iban=settings.payment_iban.replace(" ", "") or None,
        issued_by_id=staff_id,
    )
    session.add(p)
    await session.flush()
    plan = "دفعة واحدة" if installments == 1 else f"القسط الأول من {installments} أقساط"
    rows = [
        ("رقم المطالبة (اكتبه في وصف التحويل)", p.payment_number),
        (
            "العميل",
            f"{customer.company_name or customer.full_name}"
            + (f" - سجل تجاري {customer.cr_number}" if customer.cr_number else ""),
        ),
        ("الرقم الضريبي للعميل", customer.vat_number or "—"),
        ("الطلب", f"{request.request_number} - {catalog.line_label(request.insurance_type)}"),
        ("شركة التأمين", insurer.name if insurer else "—"),
        ("إجمالي القسط حسب العرض المقبول", money(total)),
        ("منها ضريبة القيمة المضافة", money(vat_amount) if vat_amount is not None else "حسب عرض الشركة"),
        ("المطلوب الآن", f"{money(p.amount_due)} ({plan})"),
        ("آخر موعد للسداد", p.due_date.isoformat()),
        ("البنك", p.bank_name or "—"),
        ("اسم المستفيد", p.account_name or "—"),
        ("الآيبان", p.iban or "تواصل مع بُعد لبيانات السداد"),
    ]
    notice = await documents.store_bytes(
        session,
        request.request_id,
        _page(
            "مطالبة بسداد قسط التأمين",
            rows,
            "تصدر الوثيقة بعد تأكيد السداد. هذه مطالبة سداد وليست فاتورة ضريبية؛ "
            "تصدر الفاتورة الضريبية للقسط من شركة التأمين.",
        ),
        f"{p.payment_number}.html",
        "text/html",
        NOTICE,
        staff_id,
        reviewed=False,
    )
    notice.review_status, notice.is_verified = "accepted", True
    p.document_id = notice.document_id
    return p


async def submit_proof(
    session: AsyncSession, p: PaymentRequest, content: bytes, file_name: str, mime: str
) -> PaymentRequest:
    if p.status not in OPEN:
        raise PaymentError(409, "PAYMENT_NOT_OPEN", "This payment request is not awaiting payment")
    doc = await documents.store_bytes(session, p.request_id, content, file_name, mime, PROOF, reviewed=False)
    doc.review_status = "accepted"
    p.proof_document_id = doc.document_id
    p.proof_submitted_at = datetime.now(UTC)
    p.proof_rejected_reason = None
    p.status = PaymentRequestStatus.PROOF_SUBMITTED
    return p


def reject_proof(p: PaymentRequest, reason: str) -> None:
    if p.status != PaymentRequestStatus.PROOF_SUBMITTED:
        raise PaymentError(409, "NO_PROOF", "There is no transfer proof to refuse")
    p.status, p.proof_rejected_reason = PaymentRequestStatus.ISSUED, reason.strip()


async def confirm(
    session: AsyncSession,
    p: PaymentRequest,
    staff_id: str,
    method: str,
    bank_reference: str | None,
    paid_on: date | None,
) -> PaymentRequest:
    if p.status not in OPEN:
        raise PaymentError(409, "PAYMENT_NOT_OPEN", "This payment request is not awaiting payment")
    if method not in METHODS:
        raise PaymentError(422, "INVALID_METHOD", "Unknown payment method")
    now = datetime.now(UTC)
    p.status = PaymentRequestStatus.PAID
    p.paid_at = datetime.combine(paid_on, now.time(), tzinfo=UTC) if paid_on else now
    p.payment_method, p.bank_reference, p.confirmed_by_id = (
        method,
        (bank_reference or "").strip() or None,
        staff_id,
    )
    request = await session.get(InsuranceRequest, p.request_id)
    customer = await session.get(Customer, request.customer_id) if request else None
    rows = [
        ("رقم المطالبة", p.payment_number),
        ("العميل", (customer.company_name or customer.full_name) if customer else "—"),
        ("المبلغ المستلم", money(p.amount_due)),
        ("طريقة السداد", METHODS[method]),
        ("مرجع البنك", p.bank_reference or "—"),
        ("تاريخ السداد", p.paid_at.date().isoformat()),
    ]
    receipt = await documents.store_bytes(
        session,
        p.request_id,
        _page("إيصال استلام دفعة", rows, "شكرًا لكم. نستكمل إصدار الوثيقة مع شركة التأمين."),
        f"إيصال-{p.payment_number}.html",
        "text/html",
        RECEIPT,
        staff_id,
        reviewed=False,
    )
    receipt.review_status, receipt.is_verified = "accepted", True
    p.receipt_document_id = receipt.document_id
    return p
