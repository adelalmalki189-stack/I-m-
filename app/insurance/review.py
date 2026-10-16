"""Automatic document review: rule-based checks written up as plain Arabic sentences.

No model is involved. `review_file` inspects one upload (real type, pages, image size,
duplicates); `review_request` puts the product's required documents next to what was
uploaded and checks the request details for inconsistencies. A language model can later
add findings through the same shapes without changing the callers.
"""

from __future__ import annotations

import re
import struct
from datetime import UTC, date, datetime
from typing import Any

from app.insurance import catalog

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIN_IMAGE_BYTES = 30 * 1024
MIN_IMAGE_SIDE = 700  # pixels: below this an ID or registration is usually unreadable

PASSED, WARNING, FAILED = "passed", "warning", "failed"
ACCEPTED, REJECTED = "accepted", "rejected"

ITEM_LABELS = {
    "missing": "لم يُرفع",
    "not_issued": "لم يصدر بعد",
    PASSED: "سليم، بانتظار تحقق الموظف",
    WARNING: "يحتاج انتباه",
    FAILED: "غير صالح",
    ACCEPTED: "تم التحقق",
    REJECTED: "مرفوض",
    "pending_signature": "بانتظار توقيع العميل",
    "signed": "موقّع إلكترونيًا",
}


# --- One file -----------------------------------------------------------------------------


def _image_size(content: bytes, mime: str) -> tuple[int, int] | None:
    if mime == "image/png" and len(content) >= 24:
        width, height = struct.unpack(">II", content[16:24])
        return width, height
    if mime == "image/jpeg":
        i = 2
        while i + 9 < len(content):
            if content[i] != 0xFF:
                i += 1
                continue
            marker = content[i + 1]
            # Start-of-frame markers carry the size (C4, C8 and CC are other tables).
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", content[i + 5 : i + 9])
                return width, height
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + struct.unpack(">H", content[i + 2 : i + 4])[0]
    return None


def _pdf_pages(content: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", content))


def review_file(
    content: bytes,
    mime: str,
    document_type: str,
    spec: catalog.Doc | None,
    duplicate_of: str | None = None,
) -> tuple[str, list[str]]:
    """Return (passed|warning|failed, findings) for one stored file."""
    findings: list[str] = []
    status = PASSED

    def warn(text: str) -> None:
        nonlocal status
        status = FAILED if status == FAILED else WARNING
        findings.append(text)

    def fail(text: str) -> None:
        nonlocal status
        status = FAILED
        findings.append(text)

    if spec is None:
        warn("لم يُحدَّد نوع هذا المستند، فصنّفه حتى يُحتسب ضمن مستندات الطلب.")

    if mime == "application/pdf":
        pages = _pdf_pages(content)
        if b"/Encrypt" in content[:4096] or b"/Encrypt" in content[-4096:]:
            warn("الملف محمي بكلمة مرور، وقد لا تتمكن شركات التأمين من فتحه.")
        if pages:
            findings.append(f"ملف PDF من {pages} صفحة.")
        else:
            warn("تعذّر التحقق من صفحات الملف، فافتحه وتأكد أنه مقروء.")
    elif mime.startswith("image/"):
        size = _image_size(content, mime)
        if size is None:
            fail("تعذّرت قراءة الصورة، وقد يكون الملف تالفًا.")
        else:
            width, height = size
            findings.append(f"صورة بدقة {width}×{height}.")
            if min(width, height) < MIN_IMAGE_SIDE:
                warn("دقة الصورة منخفضة، وقد لا تظهر البيانات بوضوح. يُفضّل تصويرها من جديد أو رفعها PDF.")
        if len(content) < MIN_IMAGE_BYTES:
            warn("حجم الصورة صغير جدًا، وغالبًا تكون مضغوطة أو غير واضحة.")
    elif mime == XLSX_MIME:
        if b"xl/" not in content:
            fail("الملف ليس جدول Excel صالحًا.")
        else:
            findings.append("جدول Excel.")
        if spec is not None and not spec.get("spreadsheet"):
            warn("هذا المستند يُطلب عادة صورة أو PDF، لا جدولًا.")

    if duplicate_of:
        warn(f"نفس الملف مرفوع سابقًا في هذا الطلب ({duplicate_of}).")
    if status == PASSED:
        findings.append("لم تظهر ملاحظات على الملف.")
    return status, findings


# --- The whole request --------------------------------------------------------------------


def _number(details: dict[str, Any], key: str) -> float | None:
    try:
        return float(details[key])
    except (KeyError, TypeError, ValueError):
        return None


def _date(details: dict[str, Any], key: str) -> date | None:
    try:
        return date.fromisoformat(str(details[key]))
    except (KeyError, ValueError):
        return None


def detail_findings(line: str, product: str, details: dict[str, Any], coverage: str | None) -> list[str]:
    """Inconsistencies in what the customer typed (not in the files)."""
    out: list[str] = []
    today = date.today()
    if line == "motor":
        count = _number(details, "vehicle_count") or 1
        year = _number(details, "vehicle_year")
        if count > 1 and product != "motor_fleet":
            out.append(f"عدد المركبات {int(count)}، والأنسب منتج «أسطول مركبات» بدل الوثيقة الفردية.")
        if product == "motor_fleet" and count < 2:
            out.append("منتج الأسطول مختار لمركبة واحدة، فتأكد من المنتج.")
        if year and year > today.year + 1:
            out.append(f"سنة الصنع {int(year)} غير منطقية.")
        if year and product == "motor_comprehensive" and today.year - year > 15:
            out.append("عمر المركبة أكثر من 15 سنة، وقد تعتذر بعض الشركات عن التأمين الشامل.")
        value, cover = _number(details, "vehicle_value"), _number({"c": coverage}, "c")
        if value and cover and cover > value * 1.5:
            out.append("مبلغ التغطية المطلوب أعلى بكثير من قيمة المركبة.")
    for key in ("start_date", "contract_start"):
        start = _date(details, key)
        if start and start < today:
            out.append("تاريخ البداية المطلوب مضى، فاتفق مع العميل على تاريخ جديد.")
    if line == "travel":
        start, end = _date(details, "start_date"), _date(details, "end_date")
        if start and end and end < start:
            out.append("تاريخ العودة قبل تاريخ السفر.")
        if start and end and product == "travel_single" and (end - start).days > 90:
            out.append("الرحلة أطول من 90 يومًا، والأنسب التأمين السنوي.")
    return out


def review_request(
    line: str,
    product: str,
    customer_type: str,
    details: dict[str, Any],
    coverage: str | None,
    documents: list[dict[str, Any]],
    letter_status: str | None,
) -> dict[str, Any]:
    """documents: dicts with document_id, document_type, file_name, review_status, findings, rejection_reason."""
    required = catalog.required_documents(line, product, customer_type)
    known = {d["key"] for d in required}
    items = []
    for spec in required:
        files = [d for d in documents if d["document_type"] == spec["key"]]
        if spec["generated"]:
            state = letter_status or "not_issued"
        elif not files:
            state = "missing"
        else:
            states = {f["review_status"] for f in files}
            # The best file decides: one accepted copy is enough even if an older one was rejected.
            state = next(s for s in (ACCEPTED, PASSED, WARNING, FAILED, REJECTED) if s in states)
        items.append(
            {
                "key": spec["key"],
                "label": spec["label"],
                "hint": spec["hint"],
                "required": spec["required"],
                "generated": spec["generated"],
                "spreadsheet": spec["spreadsheet"],
                "status": state,
                "status_label": ITEM_LABELS[state],
                "documents": files,
            }
        )

    good = {ACCEPTED, PASSED, WARNING, "signed"}
    must = [i for i in items if i["required"]]
    done = [i for i in must if i["status"] in good]
    missing = [i["label"] for i in must if i["status"] in ("missing", "not_issued", "pending_signature")]
    broken = [i["label"] for i in items if i["status"] in (FAILED, REJECTED)]
    warnings = [i["label"] for i in items if i["status"] == WARNING]
    extra = [d for d in documents if d["document_type"] not in known]
    detail_notes = detail_findings(line, product, details, coverage)

    findings: list[dict[str, str]] = []
    if missing:
        findings.append({"level": "missing", "text": "ناقص: " + "، ".join(missing) + "."})
    if broken:
        findings.append({"level": "missing", "text": "يحتاج إعادة رفع: " + "، ".join(broken) + "."})
    if warnings:
        findings.append({"level": "warning", "text": "راجع ملاحظات: " + "، ".join(warnings) + "."})
    if extra:
        findings.append({"level": "warning", "text": f"{len(extra)} مستند بدون تصنيف، فصنّفه أو احذفه."})
    findings += [{"level": "warning", "text": text} for text in detail_notes]
    verified = sum(1 for i in must if i["status"] in (ACCEPTED, "signed"))
    if not findings:
        findings.append({"level": "ok", "text": "كل المستندات المطلوبة موجودة ولم تظهر ملاحظات."})

    ready = not missing and not broken
    percent = round(100 * len(done) / len(must)) if must else 100
    if ready and verified == len(must):
        headline = "الطلب مكتمل وتم التحقق من مستنداته، وجاهز للإرسال لشركات التأمين."
    elif ready:
        headline = f"المستندات مكتملة. تحقق الموظف من {verified} من {len(must)}."
    else:
        headline = f"اكتمل {len(done)} من {len(must)} مستندات مطلوبة."
    return {
        "checked_at": datetime.now(UTC),
        "completeness": percent,
        "ready": ready,
        "verified": verified,
        "required_count": len(must),
        "headline": headline,
        "findings": findings,
        "items": items,
        "extra_documents": extra,
        "missing": missing,
    }
