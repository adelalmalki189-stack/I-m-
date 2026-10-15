"""Built-in automation rules: what triggers them, their default wording and timing.

Managers can switch a rule off, reword its message, set a WhatsApp template name, or
change its timing (stored in `automation_rules`); everything else lives here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.models import AutomationRule


@dataclass(frozen=True)
class RuleDef:
    key: str
    label: str
    description: str
    message: str | None  # None = internal rule, no customer message
    variables: tuple[str, ...] = ()
    params: dict[str, Any] = field(default_factory=dict)


SIGNATURE = "\n- بُعد للوساطة التأمينية"

RULES: dict[str, RuleDef] = {
    r.key: r
    for r in [
        RuleDef(
            "request_received",
            "تأكيد استلام الطلب",
            "عند وصول طلب جديد من بوابة العميل.",
            "مرحبًا {customer_name}، استلمنا طلبك رقم {request_number} لتأمين {line}. "
            "نجهز لك عروض الأسعار ونتواصل معك قريبًا." + SIGNATURE,
            ("customer_name", "request_number", "line"),
        ),
        RuleDef(
            "missing_info",
            "طلب استكمال بيانات",
            "عند تحويل الطلب إلى «نواقص بيانات».",
            "مرحبًا {customer_name}، نحتاج استكمال بعض البيانات لطلبك رقم {request_number} "
            "حتى نطلب لك العروض. سيتواصل معك فريقنا." + SIGNATURE,
            ("customer_name", "request_number"),
        ),
        RuleDef(
            "authorization_letter",
            "طلب توقيع خطاب التفويض",
            "عند إصدار خطاب التفويض: يصل العميل رابط صفحة المتابعة ليوقّعه إلكترونيًا.",
            "مرحبًا {customer_name}، جهزنا خطاب التفويض لطلبك رقم {request_number} حتى نطلب لك العروض "
            "من شركات التأمين. وقّعه إلكترونيًا من هنا: {track_link}" + SIGNATURE,
            ("customer_name", "request_number", "track_link"),
        ),
        RuleDef(
            "documents_missing",
            "طلب مستندات ناقصة",
            "عندما يطلب الموظف من العميل المستندات الناقصة بعد المراجعة الآلية.",
            "مرحبًا {customer_name}، لإكمال طلبك رقم {request_number} نحتاج: {missing}. "
            "ارفعها من صفحة المتابعة: {track_link}" + SIGNATURE,
            ("customer_name", "request_number", "missing", "track_link"),
        ),
        RuleDef(
            "signature_reminder",
            "تذكير توقيع خطاب التفويض",
            "إذا لم يوقّع العميل الخطاب بعد عدد الساعات المحدد من إصداره.",
            "تذكير: خطاب التفويض لطلبك رقم {request_number} بانتظار توقيعك حتى نطلب لك العروض من شركات "
            "التأمين. التوقيع يأخذ دقيقة: {track_link}" + SIGNATURE,
            ("customer_name", "request_number", "track_link"),
            {"after_hours": [24, 72]},
        ),
        RuleDef(
            "documents_reminder",
            "تذكير المستندات الناقصة",
            "إذا بقيت مستندات مطلوبة ناقصة بعد عدد الساعات المحدد من تقديم الطلب.",
            "مرحبًا {customer_name}، طلبك رقم {request_number} ما زال ينقصه: {missing}. "
            "ارفعها من هنا: {track_link}" + SIGNATURE,
            ("customer_name", "request_number", "missing", "track_link"),
            {"after_hours": [48, 120]},
        ),
        RuleDef(
            "auto_assign_sales",
            "توزيع الطلبات على المبيعات",
            "يسند كل طلب جديد بلا موظف إلى موظف المبيعات الأقل طلبات مفتوحة.",
            None,
        ),
        RuleDef(
            "auto_handoff_underwriting",
            "التحويل الآلي للاكتتاب",
            "عند اكتمال المستندات المطلوبة بلا ملاحظات وتوقيع خطاب التفويض: يحوّل الطلب إلى «قيد الاكتتاب» "
            "ويسنده للمكتتب الأقل طلبات.",
            None,
        ),
        RuleDef(
            "offers_ready",
            "العروض جاهزة للاختيار",
            "عند إرسال الموظف عروض الشركات للعميل.",
            "مرحبًا {customer_name}، وصلتنا {count} عروض لطلبك رقم {request_number}. قارنها واختر العرض "
            "المناسب ووقّع القبول إلكترونيًا من هنا: {track_link}" + SIGNATURE,
            ("customer_name", "request_number", "count", "track_link"),
        ),
        RuleDef(
            "offer_accepted",
            "تأكيد قبول العرض",
            "عند قبول العميل أحد العروض إلكترونيًا.",
            "شكرًا {customer_name}، استلمنا قبولكم لعرض {insurer} لطلب رقم {request_number} بقسط {premium}. "
            "نتواصل معكم لاستكمال الإصدار والسداد." + SIGNATURE,
            ("customer_name", "request_number", "insurer", "premium"),
        ),
        RuleDef(
            "policy_issued",
            "إصدار الوثيقة",
            "عند إصدار الوثيقة.",
            "مبروك {customer_name}، صدرت وثيقتك رقم {policy_number} من {insurer} "
            "وتسري حتى {end_date}. القسط: {premium}." + SIGNATURE,
            ("customer_name", "policy_number", "insurer", "end_date", "premium"),
        ),
        RuleDef(
            "claim_update",
            "تحديث المطالبة",
            "عند تغيّر حالة المطالبة (إرسال، اعتماد، رفض، صرف).",
            "مرحبًا {customer_name}، تحديث مطالبتك رقم {claim_number}: {claim_status}." + SIGNATURE,
            ("customer_name", "claim_number", "claim_status"),
        ),
        RuleDef(
            "renewal_reminder",
            "تذكير التجديد",
            "قبل انتهاء الوثيقة بعدد الأيام المحدد.",
            "مرحبًا {customer_name}، وثيقتك رقم {policy_number} تنتهي في {end_date}. "
            "نجهز لك عروض التجديد لتبقى محميًا بدون انقطاع." + SIGNATURE,
            ("customer_name", "policy_number", "end_date"),
            {"days": [60, 30, 7]},
        ),
        RuleDef(
            "installment_due",
            "تذكير قسط قادم",
            "قبل تاريخ استحقاق القسط.",
            "تذكير: قسط بقيمة {amount} للوثيقة {policy_number} يستحق في {due_date}." + SIGNATURE,
            ("amount", "policy_number", "due_date"),
            {"days_before": 3},
        ),
        RuleDef(
            "installment_overdue",
            "قسط متأخر",
            "بعد تاريخ الاستحقاق إذا لم يُسدَّد.",
            "مرحبًا {customer_name}، قسط بقيمة {amount} للوثيقة {policy_number} متأخر منذ {due_date}. "
            "نرجو السداد لتجنب انقطاع التغطية." + SIGNATURE,
            ("customer_name", "amount", "policy_number", "due_date"),
            {"days_after": [1, 7]},
        ),
        RuleDef(
            "auto_renewal_request",
            "فتح طلب تجديد تلقائيًا",
            "يفتح طلب تجديد بنفس بيانات الخطر قبل انتهاء الوثيقة بعدد الأيام المحدد.",
            None,
            (),
            {"days": 45},
        ),
        RuleDef(
            "expire_policies",
            "تسجيل الوثائق المنتهية",
            "يحوّل الوثائق التي انتهت مدتها إلى «منتهية».",
            None,
        ),
        RuleDef(
            "insurer_followup",
            "متابعة الشركات المتأخرة",
            "إذا لم يصل عرض بعد عدد الساعات المحدد: يرسل بريد متابعة للشركة (إن كان البريد مفعّلًا) "
            "أو يعلّم الرسالة «تحتاج متابعة».",
            None,
            (),
            {"hours": 48, "max_followups": 2},
        ),
    ]
}

_PLACEHOLDER = re.compile(r"\{\s*([a-z_]+)\s*\}")


@dataclass
class EffectiveRule:
    key: str
    enabled: bool
    message: str | None
    whatsapp_template: str | None
    params: dict[str, Any]


async def get_rule(session: AsyncSession, key: str) -> EffectiveRule:
    base = RULES[key]
    row = await session.get(AutomationRule, key)
    params = dict(base.params)
    if row and row.params:
        params.update(json.loads(row.params))
    return EffectiveRule(
        key=key,
        enabled=row.enabled if row else True,
        message=(row.message if row and row.message else base.message),
        whatsapp_template=row.whatsapp_template if row else None,
        params=params,
    )


def render(text: str, context: dict[str, Any]) -> str:
    return _PLACEHOLDER.sub(lambda m: str(context.get(m.group(1), m.group(0))), text)


def unknown_variables(key: str, text: str) -> list[str]:
    return sorted({m.group(1) for m in _PLACEHOLDER.finditer(text)} - set(RULES[key].variables))
