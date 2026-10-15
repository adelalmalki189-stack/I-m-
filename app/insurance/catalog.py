"""BUD product catalog: every insurance line, its products, and the risk details
the customer must provide for it.

This is the single source of truth for the portal form (served via
GET /insurance/catalog), for validating portal submissions, and for the
template `category` used when emailing insurers (category == line key).
"""

from __future__ import annotations

from typing import Any

Field = dict[str, Any]


def _f(key: str, label: str, type_: str = "text", required: bool = True, **extra: Any) -> Field:
    return {"key": key, "label": label, "type": type_, "required": required, **extra}


CITY = _f(
    "city",
    "المدينة",
    "select",
    options=[
        "الرياض",
        "جدة",
        "مكة المكرمة",
        "المدينة المنورة",
        "الدمام",
        "الخبر",
        "أبها",
        "تبوك",
        "القصيم",
        "حائل",
        "جازان",
        "نجران",
        "الطائف",
        "أخرى",
    ],
)
START = _f("start_date", "تاريخ بداية التغطية المطلوب", "date")

LINES: list[dict[str, Any]] = [
    {
        "key": "motor",
        "label": "المركبات",
        "icon": "▣",
        "products": [
            {"key": "motor_tpl", "label": "ضد الغير"},
            {"key": "motor_comprehensive", "label": "شامل"},
            {"key": "motor_fleet", "label": "أسطول مركبات"},
        ],
        "fields": [
            _f("vehicle_make", "الشركة المصنعة", placeholder="مثال: تويوتا"),
            _f("vehicle_model", "الطراز", placeholder="مثال: كامري"),
            _f("vehicle_year", "سنة الصنع", "number", min=1980, max=2030),
            _f("vehicle_count", "عدد المركبات", "number", min=1),
            _f("vehicle_value", "القيمة التقديرية (ر.س)", "number", required=False, min=0),
            _f("vehicle_usage", "نوع الاستخدام", "select", options=["خاص", "تجاري", "نقل", "تأجير"]),
            _f("sequence_number", "الرقم التسلسلي أو بطاقة الجمارك", required=False),
        ],
    },
    {
        "key": "medical",
        "label": "التأمين الطبي",
        "icon": "＋",
        "products": [
            {"key": "medical_individual", "label": "أفراد وعائلات"},
            {"key": "medical_group", "label": "جماعي للمنشآت"},
            {"key": "medical_visitors", "label": "تأمين الزيارة"},
        ],
        "fields": [
            _f("members_count", "عدد المؤمَّن لهم", "number", min=1),
            _f("network_class", "فئة الشبكة الطبية", "select", options=["VIP", "A", "B", "C", "أحتاج استشارة"]),
            _f("dependents_included", "يشمل التابعين؟", "select", options=["نعم", "لا"]),
            _f("current_insurer", "شركة التأمين الحالية (إن وجدت)", required=False),
            START,
        ],
    },
    {
        "key": "property",
        "label": "الممتلكات",
        "icon": "⌂",
        "products": [
            {"key": "property_fire", "label": "الحريق والأخطار المضافة"},
            {"key": "property_all_risks", "label": "جميع أخطار الممتلكات"},
            {"key": "property_home", "label": "المنازل"},
        ],
        "fields": [
            _f("property_type", "نوع العقار", "select", options=["سكني", "تجاري", "صناعي", "مستودع", "أخرى"]),
            CITY,
            _f("sum_insured", "قيمة الأصول المراد تأمينها (ر.س)", "number", min=1),
            _f("occupancy", "طبيعة النشاط أو الإشغال", required=False),
            _f(
                "fire_protection",
                "وسائل الحماية من الحريق",
                "select",
                required=False,
                options=["رشاشات", "طفايات وإنذار", "لا يوجد"],
            ),
        ],
    },
    {
        "key": "engineering",
        "label": "التأمين الهندسي",
        "icon": "⚒",
        "products": [
            {"key": "eng_car", "label": "جميع أخطار المقاولين"},
            {"key": "eng_ear", "label": "جميع أخطار التركيب"},
            {"key": "eng_machinery", "label": "عطب الآلات"},
            {"key": "eng_plant", "label": "معدات المقاولين"},
        ],
        "fields": [
            _f("project_description", "وصف المشروع أو المعدات", "textarea"),
            _f("contract_value", "قيمة العقد أو المعدات (ر.س)", "number", min=1),
            _f("duration_months", "مدة المشروع (بالأشهر)", "number", required=False, min=1),
            CITY,
        ],
    },
    {
        "key": "marine",
        "label": "التأمين البحري والنقل",
        "icon": "⚓",
        "products": [
            {"key": "marine_cargo_single", "label": "شحنة واحدة"},
            {"key": "marine_cargo_open", "label": "وثيقة مفتوحة للشحنات"},
            {"key": "marine_hull", "label": "أجسام السفن"},
            {"key": "marine_inland", "label": "النقل البري"},
        ],
        "fields": [
            _f("goods_description", "وصف البضائع أو الوسيلة", "textarea"),
            _f("shipment_value", "قيمة الشحنة أو الحد السنوي (ر.س)", "number", min=1),
            _f("origin", "بلد أو مدينة الشحن"),
            _f("destination", "الوجهة"),
            _f("transport_mode", "وسيلة النقل", "select", options=["بحري", "جوي", "بري", "متعدد"]),
        ],
    },
    {
        "key": "liability",
        "label": "المسؤوليات",
        "icon": "⚖",
        "products": [
            {"key": "liability_general", "label": "المسؤولية العامة"},
            {"key": "liability_professional", "label": "المسؤولية المهنية"},
            {"key": "liability_medical_malpractice", "label": "الأخطاء الطبية"},
            {"key": "liability_employers", "label": "مسؤولية صاحب العمل"},
            {"key": "liability_product", "label": "مسؤولية المنتجات"},
            {"key": "liability_dno", "label": "مسؤولية أعضاء مجلس الإدارة"},
            {"key": "liability_cyber", "label": "المخاطر السيبرانية"},
        ],
        "fields": [
            _f("activity", "النشاط أو المهنة"),
            _f("annual_revenue", "الإيرادات السنوية التقريبية (ر.س)", "number", required=False, min=0),
            _f("employees_count", "عدد الموظفين", "number", required=False, min=0),
            _f("limit_required", "حد المسؤولية المطلوب (ر.س)", "number", min=1),
            _f("claims_history", "مطالبات سابقة خلال 3 سنوات", "select", options=["لا يوجد", "نعم"]),
        ],
    },
    {
        "key": "general_accident",
        "label": "الحوادث العامة",
        "icon": "✚",
        "products": [
            {"key": "ga_personal_accident", "label": "الحوادث الشخصية"},
            {"key": "ga_money", "label": "الأموال"},
            {"key": "ga_fidelity", "label": "خيانة الأمانة"},
        ],
        "fields": [
            _f("insured_count", "عدد الأشخاص أو المواقع", "number", min=1),
            _f("sum_insured", "مبلغ التأمين (ر.س)", "number", min=1),
            _f("description", "تفاصيل إضافية", "textarea", required=False),
        ],
    },
    {
        "key": "protection",
        "label": "الحماية والادخار",
        "icon": "◈",
        "products": [
            {"key": "protection_group_life", "label": "الحماية الجماعية للموظفين"},
            {"key": "protection_credit_life", "label": "حماية الائتمان"},
            {"key": "protection_individual", "label": "الحماية والادخار للأفراد"},
        ],
        "fields": [
            _f("members_count", "عدد المؤمَّن لهم", "number", min=1),
            _f("sum_insured_per_member", "مبلغ التغطية لكل فرد (ر.س)", "number", min=1),
            START,
        ],
    },
    {
        "key": "travel",
        "label": "السفر",
        "icon": "↗",
        "products": [
            {"key": "travel_single", "label": "رحلة واحدة"},
            {"key": "travel_annual", "label": "سنوي متعدد الرحلات"},
        ],
        "fields": [
            _f("destination", "الوجهة"),
            _f("travelers", "عدد المسافرين", "number", min=1, max=50),
            _f("start_date", "تاريخ السفر", "date"),
            _f("end_date", "تاريخ العودة", "date"),
        ],
    },
    {
        "key": "domestic_workers",
        "label": "العمالة المنزلية",
        "icon": "♙",
        "products": [{"key": "dw_contract", "label": "تأمين عقد العمالة المنزلية"}],
        "fields": [
            _f("workers_count", "عدد العمالة", "number", min=1),
            _f("worker_nationality", "الجنسية"),
            _f("contract_start", "تاريخ بداية العقد", "date"),
        ],
    },
]

Doc = dict[str, Any]


def _d(
    key: str,
    label: str,
    hint: str = "",
    required: bool = True,
    products: list[str] | None = None,
    who: str = "all",
    generated: bool = False,
    spreadsheet: bool = False,
) -> Doc:
    """A document the request needs.

    products: only for these product keys (None = every product of the line).
    who: "individual", "business" or "all" (customer type).
    generated: BUD issues it (the authorization letter); the customer signs, never uploads.
    spreadsheet: an Excel/CSV list is accepted besides PDF and images.
    """
    return {
        "key": key,
        "label": label,
        "hint": hint,
        "required": required,
        "products": products,
        "who": who,
        "generated": generated,
        "spreadsheet": spreadsheet,
    }


AUTHORIZATION_LETTER = "authorization_letter"
# Every request: who the customer is, and the letter authorizing BUD to ask insurers on their behalf.
COMMON_DOCUMENTS = [
    _d("national_id", "الهوية الوطنية أو الإقامة", "صورة واضحة للوجهين، سارية المفعول", who="individual"),
    _d("commercial_registration", "السجل التجاري", "ساري المفعول، ويظهر فيه النشاط", who="business"),
    _d(
        AUTHORIZATION_LETTER,
        "خطاب التفويض",
        "يصدره النظام ويوقّعه العميل إلكترونيًا من صفحة متابعة الطلب",
        generated=True,
    ),
]
LINE_DOCUMENTS: dict[str, list[Doc]] = {
    "motor": [
        _d(
            "vehicle_registration",
            "استمارة المركبة",
            "سارية، ويظهر فيها الرقم التسلسلي",
            products=["motor_tpl", "motor_comprehensive"],
        ),
        _d("driving_license", "رخصة القيادة", "للسائق الرئيسي", products=["motor_tpl", "motor_comprehensive"]),
        _d("customs_card", "البطاقة الجمركية", "للمركبات الجديدة غير المسجلة", required=False),
        _d(
            "vehicle_photos",
            "صور المركبة",
            "أربع جهات وعداد المسافة، للتأمين الشامل",
            required=False,
            products=["motor_comprehensive"],
        ),
        _d(
            "vehicle_list",
            "كشف المركبات",
            "لكل مركبة: الرقم التسلسلي، الطراز، السنة، القيمة",
            products=["motor_fleet"],
            spreadsheet=True,
        ),
        _d("previous_policy", "الوثيقة السابقة", "إن وجدت، مع سجل المطالبات", required=False),
    ],
    "medical": [
        _d(
            "employee_census",
            "كشف الموظفين والتابعين",
            "الاسم، الهوية، تاريخ الميلاد، الجنس، الفئة",
            products=["medical_group"],
            spreadsheet=True,
        ),
        _d("members_ids", "هويات المؤمَّن لهم", "لكل أفراد العائلة", products=["medical_individual"]),
        _d("passport", "جواز السفر", "صفحة البيانات", products=["medical_visitors"]),
        _d("visa", "التأشيرة", "", products=["medical_visitors"]),
        _d(
            "claims_experience",
            "تقرير الخبرة التأمينية",
            "من شركة التأمين الحالية لآخر سنتين",
            required=False,
            products=["medical_group"],
        ),
    ],
    "property": [
        _d("ownership_deed", "صك الملكية أو عقد الإيجار", ""),
        _d("asset_schedule", "جدول الأصول وقيمها", "المبنى، المحتويات، المخزون", spreadsheet=True),
        _d(
            "civil_defense",
            "رخصة الدفاع المدني",
            "",
            required=False,
            products=["property_fire", "property_all_risks"],
        ),
        _d("site_photos", "صور الموقع", "", required=False),
    ],
    "engineering": [
        _d("project_contract", "عقد المشروع", "", products=["eng_car", "eng_ear"]),
        _d("bill_of_quantities", "جدول الكميات", "", products=["eng_car", "eng_ear"], spreadsheet=True),
        _d("project_schedule", "الجدول الزمني للمشروع", "", required=False, products=["eng_car", "eng_ear"]),
        _d(
            "equipment_list",
            "كشف المعدات",
            "النوع، الرقم التسلسلي، سنة الصنع، القيمة",
            products=["eng_machinery", "eng_plant"],
            spreadsheet=True,
        ),
    ],
    "marine": [
        _d(
            "commercial_invoice",
            "الفاتورة التجارية",
            "",
            products=["marine_cargo_single", "marine_inland"],
        ),
        _d("bill_of_lading", "بوليصة الشحن", "", required=False, products=["marine_cargo_single"]),
        _d("packing_list", "قائمة التعبئة", "", required=False, products=["marine_cargo_single"]),
        _d(
            "shipment_forecast",
            "تقدير الشحنات السنوية",
            "عدد الشحنات وقيمتها المتوقعة",
            products=["marine_cargo_open"],
            spreadsheet=True,
        ),
        _d("vessel_registration", "شهادة تسجيل السفينة", "", products=["marine_hull"]),
        _d("class_certificate", "شهادة التصنيف", "", products=["marine_hull"]),
    ],
    "liability": [
        _d("activity_license", "رخصة مزاولة النشاط", ""),
        _d(
            "professional_license",
            "الترخيص المهني",
            "هيئة التخصصات الصحية أو الهيئة المهنية",
            products=["liability_professional", "liability_medical_malpractice"],
        ),
        _d("financial_statements", "القوائم المالية", "آخر سنة", required=False),
        _d("claims_history", "سجل المطالبات", "آخر 3 سنوات", required=False),
    ],
    "general_accident": [
        _d(
            "insured_list",
            "كشف المؤمَّن لهم",
            "الاسم، الهوية، المهنة",
            products=["ga_personal_accident", "ga_fidelity"],
            spreadsheet=True,
        ),
        _d("cash_procedures", "إجراءات نقل وحفظ الأموال", "", required=False, products=["ga_money"]),
    ],
    "protection": [
        _d(
            "members_list",
            "كشف المؤمَّن لهم",
            "الاسم، الهوية، تاريخ الميلاد، مبلغ التغطية",
            products=["protection_group_life", "protection_credit_life"],
            spreadsheet=True,
        ),
        _d("health_declaration", "إقرار صحي", "", required=False, products=["protection_individual"]),
    ],
    "travel": [
        _d("passport", "جواز السفر", "لكل مسافر، صفحة البيانات"),
        _d("visa", "التأشيرة", "إن كانت الوجهة تتطلبها", required=False),
    ],
    "domestic_workers": [
        _d("labor_contract", "عقد العمل", "من منصة مساند"),
        _d("worker_passport", "جواز العامل", "صفحة البيانات"),
    ],
}


def required_documents(line_key: str, product_key: str, customer_type: str) -> list[Doc]:
    """The documents this request needs, in display order (common first)."""
    return [
        doc
        for doc in COMMON_DOCUMENTS + LINE_DOCUMENTS.get(line_key, [])
        if (doc["products"] is None or product_key in doc["products"]) and doc["who"] in ("all", customer_type)
    ]


def document_label(key: str) -> str:
    for doc in COMMON_DOCUMENTS + [d for docs in LINE_DOCUMENTS.values() for d in docs]:
        if doc["key"] == key:
            return str(doc["label"])
    return "مستند آخر"


for _line in LINES:
    _line["documents"] = COMMON_DOCUMENTS + LINE_DOCUMENTS.get(_line["key"], [])

LINES_BY_KEY = {line["key"]: line for line in LINES}


def line_label(key: str) -> str:
    line = LINES_BY_KEY.get(key)
    return line["label"] if line else key


def product_label(line_key: str, product_key: str) -> str:
    line = LINES_BY_KEY.get(line_key)
    for product in (line or {}).get("products", []):
        if product["key"] == product_key:
            return product["label"]
    return product_key


def validate_details(line_key: str, product_key: str, details: dict[str, Any]) -> list[str]:
    """Return a list of Arabic error messages; empty when the details are complete."""
    line = LINES_BY_KEY.get(line_key)
    if line is None:
        return [f"فرع التأمين غير معروف: {line_key}"]
    if product_key not in {p["key"] for p in line["products"]}:
        return [f"المنتج غير معروف لفرع {line['label']}: {product_key}"]
    errors = []
    for field in line["fields"]:
        value = details.get(field["key"])
        if field["required"] and (value is None or str(value).strip() == ""):
            errors.append(f"الحقل مطلوب: {field['label']}")
        elif field.get("options") and value not in (None, "") and value not in field["options"]:
            errors.append(f"قيمة غير مقبولة في: {field['label']}")
    return errors


def format_details(line_key: str, details: dict[str, Any]) -> str:
    """Human-readable risk details, one 'label: value' per line, in catalog order."""
    line = LINES_BY_KEY.get(line_key)
    labels = {f["key"]: f["label"] for f in (line or {}).get("fields", [])}
    ordered = [k for k in labels if k in details] + [k for k in details if k not in labels]
    return "\n".join(f"- {labels.get(k, k)}: {details[k]}" for k in ordered if str(details[k]).strip())
