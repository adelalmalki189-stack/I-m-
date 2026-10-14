"""Request lifecycle rules and insurer-email template rendering."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.insurance.models import InsuranceRequest, InsuranceRequestStatus

S = InsuranceRequestStatus

# Which statuses a request may move to from each status. POLICY_ISSUED is terminal;
# CLOSED (lost/cancelled) can be reopened back into review.
ALLOWED_TRANSITIONS: dict[InsuranceRequestStatus, set[InsuranceRequestStatus]] = {
    S.NEW: {S.NEEDS_INFO, S.UNDER_REVIEW, S.READY_FOR_QUOTE, S.CLOSED},
    S.NEEDS_INFO: {S.UNDER_REVIEW, S.READY_FOR_QUOTE, S.CLOSED},
    S.UNDER_REVIEW: {S.NEEDS_INFO, S.READY_FOR_QUOTE, S.CLOSED},
    S.READY_FOR_QUOTE: {S.NEEDS_INFO, S.UNDER_REVIEW, S.QUOTE_SELECTED, S.CLOSED},
    S.QUOTE_SELECTED: {S.READY_FOR_QUOTE, S.POLICY_ISSUED, S.CLOSED},
    S.POLICY_ISSUED: set(),
    S.CLOSED: {S.UNDER_REVIEW},
}

OPEN_STATUSES = {S.NEW, S.NEEDS_INFO, S.UNDER_REVIEW, S.READY_FOR_QUOTE, S.QUOTE_SELECTED}


class InvalidTransition(Exception):
    def __init__(self, current: InsuranceRequestStatus, target: InsuranceRequestStatus):
        super().__init__(f"Cannot move a request from '{current.value}' to '{target.value}'")
        self.current, self.target = current, target


def allowed_next(status: InsuranceRequestStatus) -> list[str]:
    return sorted(s.value for s in ALLOWED_TRANSITIONS[status])


def transition(request: InsuranceRequest, target: InsuranceRequestStatus) -> None:
    """Move `request` to `target`, or raise InvalidTransition. Same-status is a no-op."""
    if request.status == target:
        return
    if target not in ALLOWED_TRANSITIONS[request.status]:
        raise InvalidTransition(request.status, target)
    request.status = target
    request.last_status_change_at = datetime.now(UTC)


# --- Templates -------------------------------------------------------------

TEMPLATE_VARIABLES: dict[str, str] = {
    "request_number": "رقم الطلب",
    "customer_name": "اسم العميل أو المنشأة",
    "customer_type": "نوع العميل",
    "insurance_type": "فرع التأمين",
    "product_type": "المنتج",
    "coverage_amount": "مبلغ التغطية",
    "risk_details": "تفاصيل الخطر كما أدخلها العميل",
    "insurer_name": "اسم شركة التأمين",
    "insurer_contact": "اسم مسؤول التواصل في الشركة",
    "notes": "ملاحظات الطلب",
    "today": "تاريخ اليوم",
}

_PLACEHOLDER = re.compile(r"\{\s*([a-z_]+)\s*\}")


def render_template(text: str, context: dict[str, str]) -> str:
    """Replace {name} placeholders with values from `context`.

    Unknown placeholders are left as-is (so a typo in a user-edited template is
    visible in the preview instead of crashing the send).
    """
    return _PLACEHOLDER.sub(lambda m: str(context[m.group(1)]) if m.group(1) in context else m.group(0), text)


def unknown_placeholders(text: str) -> list[str]:
    return sorted({m.group(1) for m in _PLACEHOLDER.finditer(text)} - TEMPLATE_VARIABLES.keys())


DEFAULT_SUBJECT = "طلب عرض سعر {insurance_type} - {product_type} - {request_number}"
DEFAULT_BODY = """السادة {insurer_name} المحترمين،
تحية طيبة وبعد،

نأمل تزويدنا بعرض سعر للطلب التالي:

رقم الطلب: {request_number}
العميل: {customer_name} ({customer_type})
الفرع والمنتج: {insurance_type} - {product_type}
مبلغ التغطية المطلوب: {coverage_amount}

تفاصيل الخطر:
{risk_details}

ملاحظات: {notes}

نرجو أن يتضمن العرض القسط وحدود التغطية والتحمل والاستثناءات ومدة صلاحية العرض.
المستندات المتوفرة مرفقة بهذه الرسالة.

مع خالص التحية،
فريق الاكتتاب - بُعد للوساطة التأمينية
"""
