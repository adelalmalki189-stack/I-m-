"""Authorization letter: BUD issues it, the customer signs it electronically.

Signing happens on the tracking page, after the customer proved the request's mobile
number with a one-time code. What gets signed is frozen: the exact text, the signer's
typed name, the last 4 digits of their ID, the verified mobile, the time, IP and browser.
A sha256 over that record is stored and printed on the signed copy, and the verification
code lets an insurer check the letter on the public verification page.
"""

from __future__ import annotations

import hashlib
import html
import json
import secrets
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.insurance import catalog, documents
from app.insurance.models import (
    AuthorizationLetter,
    AuthorizationLetterStatus,
    Customer,
    InsuranceRequest,
)

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I


def new_verification_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def mask_mobile(mobile: str | None) -> str:
    return f"{mobile[:4]}•••{mobile[-3:]}" if mobile and len(mobile) > 7 else "—"


def ar_date(value: date) -> str:
    return value.strftime("%Y/%m/%d")


def letter_text(
    letter_number: str, request: InsuranceRequest, customer: Customer, issued: date, valid_until: date
) -> str:
    company = customer.company_name or customer.full_name
    cr = f"، سجل تجاري رقم {customer.cr_number}" if customer.cr_number else ""
    representative = request.contact_name or customer.full_name
    title = request.contact_title or customer.contact_title
    role = f" ({title})" if title else ""
    return "\n".join(
        [
            f"خطاب تفويض رقم {letter_number}",
            f"التاريخ: {ar_date(issued)}",
            "",
            "إلى من يهمه الأمر من شركات التأمين المرخّصة،",
            "",
            f"نحن {company}{cr}، ويمثلنا في هذا التفويض {representative}{role}، "
            f"صاحب الجوال {mask_mobile(request.contact_mobile or customer.phone)}،",
            f"نفوّض {settings.broker_legal_name} فيما يخص طلب التأمين رقم {request.request_number}"
            f" ({catalog.line_label(request.insurance_type)} - "
            f"{catalog.product_label(request.insurance_type, request.product_type)}) بما يلي:",
            "1. طلب عروض الأسعار من شركات التأمين نيابةً عنّا، وتزويدها ببيانات الطلب ومستنداته.",
            "2. التفاوض على الأسعار والشروط، ومقارنة العروض وعرضها علينا.",
            "3. استلام المراسلات المتعلقة بالطلب من شركات التأمين.",
            "",
            "ولا يشمل هذا التفويض قبول أي وثيقة أو دفع أي قسط إلا بموافقتنا الصريحة.",
            f"يسري هذا التفويض حتى {ar_date(valid_until)}، ولنا إلغاؤه في أي وقت بإشعار الوسيط.",
            "ويقرّ الموقّع بأنه مخوّل بالتوقيع عن المنشأة.",
        ]
    )


async def current(session: AsyncSession, request_id: str) -> AuthorizationLetter | None:
    """The live letter of a request (signed or awaiting signature), newest first."""
    return (
        (
            await session.execute(
                select(AuthorizationLetter)
                .where(
                    AuthorizationLetter.request_id == request_id,
                    AuthorizationLetter.status != AuthorizationLetterStatus.REVOKED,
                )
                .order_by(AuthorizationLetter.created_at.desc())
            )
        )
        .scalars()
        .first()
    )


async def issue(
    session: AsyncSession, request: InsuranceRequest, customer: Customer, issued_by_id: str | None
) -> AuthorizationLetter:
    """Issue a new letter awaiting signature; any previous letter of the request is revoked."""
    for old in (
        await session.execute(
            select(AuthorizationLetter).where(
                AuthorizationLetter.request_id == request.request_id,
                AuthorizationLetter.status != AuthorizationLetterStatus.REVOKED,
            )
        )
    ).scalars():
        old.status = AuthorizationLetterStatus.REVOKED
    today = datetime.now(UTC).date()
    valid_until = today + timedelta(days=settings.authorization_valid_days)
    number = f"AUTH-{today.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
    letter = AuthorizationLetter(
        letter_number=number,
        request_id=request.request_id,
        status=AuthorizationLetterStatus.PENDING_SIGNATURE,
        body_text=letter_text(number, request, customer, today, valid_until),
        valid_until=valid_until,
        issued_by_id=issued_by_id,
        verification_code=new_verification_code(),
    )
    session.add(letter)
    await session.flush()
    return letter


def utc_seconds(value: datetime | None) -> str | None:
    # Same string whether the database hands the time back aware (PostgreSQL) or naive (SQLite).
    if value is None:
        return None
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def signature_hash(letter: AuthorizationLetter) -> str:
    record = {
        "letter_number": letter.letter_number,
        "body_text": letter.body_text,
        "signer_name": letter.signer_name,
        "signer_id_last4": letter.signer_id_last4,
        "signed_mobile": letter.signed_mobile,
        # Added later; only part of the record when set, so earlier signatures still verify.
        **({"signer_title": letter.signer_title} if letter.signer_title else {}),
        "signed_at": utc_seconds(letter.signed_at),
    }
    return hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def verify_url(letter: AuthorizationLetter) -> str:
    return f"{settings.public_base_url}/dashboard/verify.html?code={letter.verification_code}"


def _signed_html(letter: AuthorizationLetter) -> str:
    signed_at = letter.signed_at.astimezone(UTC).strftime("%Y/%m/%d %H:%M UTC") if letter.signed_at else ""
    body = "".join(f"<p>{html.escape(line) or '&nbsp;'}</p>" for line in letter.body_text.split("\n"))
    rows = [
        ("الموقّع", letter.signer_name or ""),
        ("صفته في المنشأة", letter.signer_title or "—"),
        ("آخر 4 أرقام من الهوية", f"••••••{letter.signer_id_last4}"),
        ("الجوال المتحقَّق منه برمز لمرة واحدة", mask_mobile(letter.signed_mobile)),
        ("وقت التوقيع", signed_at),
        ("رمز التحقق", letter.verification_code),
        ("بصمة التوقيع (SHA-256)", letter.content_hash or ""),
    ]
    table = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>" for k, v in rows)
    url = html.escape(verify_url(letter))
    return f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<title>{html.escape(letter.letter_number)}</title>
<style>body{{font-family:Tahoma,sans-serif;max-width:720px;margin:32px auto;padding:0 16px;
line-height:1.9;color:#111}}
p{{margin:0}}.sig{{margin-top:28px;padding:14px;border:1px solid #999;border-radius:6px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:6px;border-bottom:1px solid #ddd;text-align:right}}
th{{width:42%;color:#555;font-weight:normal}}td{{word-break:break-all}}.verify{{margin-top:10px;font-size:12px}}</style>
</head><body>{body}
<div class="sig"><b>توقيع إلكتروني</b><table>{table}</table>
<div class="verify">للتحقق من صحة هذا الخطاب: <a href="{url}">{url}</a></div></div>
</body></html>"""


async def sign(
    session: AsyncSession,
    letter: AuthorizationLetter,
    signer_name: str,
    id_number: str,
    mobile: str,
    signer_title: str | None,
    ip: str,
    user_agent: str,
) -> AuthorizationLetter:
    letter.signer_name = signer_name.strip()
    letter.signer_id_last4 = id_number[-4:]
    letter.signer_title = (signer_title or "").strip() or None
    letter.signed_mobile = mobile
    letter.signed_at = datetime.now(UTC)
    letter.signed_ip = ip
    letter.signed_user_agent = user_agent[:255]
    letter.content_hash = signature_hash(letter)
    letter.status = AuthorizationLetterStatus.SIGNED
    copy = await documents.store_bytes(
        session,
        letter.request_id,
        _signed_html(letter).encode(),
        f"{letter.letter_number}.html",
        "text/html",
        catalog.AUTHORIZATION_LETTER,
        reviewed=False,
    )
    copy.review_status = "accepted"
    copy.is_verified = True
    copy.reviewed_at = letter.signed_at
    letter.document_id = copy.document_id
    await session.flush()
    return letter
