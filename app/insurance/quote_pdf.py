"""Read an insurer's quotation PDF into a draft quote for the underwriter to confirm.

By default the text layer is read with pypdf and the offer is found by rules: labelled
amounts (premium, VAT, limit), the deductible, validity, the insurer's reference, the
commission, per-class premiums and the medical network class, in Arabic or English.
When AI extraction is switched on, Claude reads the PDF itself (scans included).
Either way the result is a draft (needs_review) until an underwriter confirms it.
"""

from __future__ import annotations

import io
import re
import unicodedata
from datetime import date, timedelta
from typing import Literal

from pypdf import PdfReader

from app.automation.inbound import QuoteExtraction

MAX_PAGES = 40
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUMBER = r"(\d{1,3}(?:[,٬]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"


class PdfUnreadable(Exception):
    pass


def pdf_text(content: bytes) -> str:
    """The PDF's text, with Arabic presentation forms folded and digits made Western."""
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise PdfUnreadable("الملف محمي بكلمة مرور")
        pages = [page.extract_text() or "" for page in reader.pages[:MAX_PAGES]]
    except PdfUnreadable:
        raise
    except Exception as exc:  # pypdf raises many kinds for a broken file
        raise PdfUnreadable("تعذّرت قراءة ملف PDF") from exc
    return unicodedata.normalize("NFKC", "\n".join(pages)).translate(_DIGITS)


def _norm(text: str) -> str:
    text = re.sub(r"[ً-ْـ]", "", text.lower())
    return re.sub("[أإآ]", "ا", text).replace("ة", "ه").replace("ى", "ي")


def _lines(text: str) -> list[str]:
    """Each line as written and reversed: some PDFs store Arabic in visual (reversed) order."""
    out = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            out.append(line)
    return out


def _has(line: str, labels: tuple[str, ...]) -> bool:
    n = _norm(line)
    reversed_line = n[::-1]
    return any(_norm(label) in n or _norm(label) in reversed_line for label in labels)


def _amounts(line: str) -> list[float]:
    values = []
    for m in re.finditer(_NUMBER, line):
        raw = m.group(1).replace(",", "").replace("٬", "")
        # Reversed lines turn "4,200.00" into "00.002,4"; skip what can't be an amount.
        try:
            value = float(raw)
        except ValueError:
            continue
        if not re.fullmatch(r"(19|20)\d\d", raw):  # a year is not an amount
            values.append(value)
    return values


def _labelled_amount(lines: list[str], labels: tuple[str, ...], exclude: tuple[str, ...] = ()) -> float | None:
    """The largest amount on the first line carrying one of `labels` (and none of `exclude`),
    or on the next line when the label stands alone."""
    for i, line in enumerate(lines):
        if _has(line, labels) and not (exclude and _has(line, exclude)):
            values = [v for v in _amounts(line) if v >= 1] or (
                [v for v in _amounts(lines[i + 1]) if v >= 1] if i + 1 < len(lines) else []
            )
            if values:
                return max(values)
    return None


def _labelled_text(lines: list[str], labels: tuple[str, ...]) -> str | None:
    for i, line in enumerate(lines):
        if _has(line, labels):
            value = re.split(r"[:：]", line, maxsplit=1)[-1].strip() if ":" in line or "：" in line else ""
            if not value and i + 1 < len(lines):
                value = lines[i + 1]
            return value[:300] or None
    return None


def _date(text: str) -> date | None:
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text) or re.search(
        r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", text
    )
    if not m:
        return None
    a, b, c = (int(x) for x in m.groups())
    year, month, day = (a, b, c) if a > 31 else (c, b, a)
    try:
        return date(year, month, day) if 2000 <= year <= 2100 else None
    except ValueError:
        return None


TOTAL = (
    "total premium",
    "gross premium",
    "total amount",
    "premium including vat",
    "اجمالي القسط",
    "القسط الاجمالي",
    "القسط شامل الضريبه",
    "المبلغ الاجمالي",
    "الاجمالي",
)
PREMIUM = ("annual premium", "net premium", "premium", "القسط السنوي", "صافي القسط", "قيمه القسط", "القسط")
VAT = ("vat", "value added tax", "ضريبه القيمه المضافه", "الضريبه")
LIMIT = (
    "sum insured",
    "limit of indemnity",
    "annual limit",
    "overall limit",
    "limit",
    "coverage limit",
    "مبلغ التامين",
    "حد التغطيه",
    "الحد الاقصي",
    "حد التعويض",
    "قيمه التغطيه",
)
DEDUCTIBLE = ("deductible", "excess", "co-pay", "copay", "التحمل", "نسبه التحمل", "قيمه التحمل")
VALID = (
    "valid until",
    "valid till",
    "validity",
    "expiry date",
    "صالح حتي",
    "صلاحيه العرض",
    "ساري حتي",
    "العرض صالح",
)
REFERENCE = (
    "quotation no",
    "quote no",
    "quotation number",
    "reference no",
    "ref no",
    "رقم العرض",
    "رقم عرض السعر",
    "المرجع",
)
COMMISSION = ("commission", "brokerage", "العموله", "عموله الوسيط")
NETWORK = ("network", "الشبكه", "شبكه مقدمي الخدمه")
EXCLUSIONS = ("exclusions", "الاستثناءات")
CONDITIONS = ("conditions", "subjectivities", "الشروط")
DECLINE = ("regret", "unable to quote", "decline", "نعتذر", "نأسف", "الاعتذار عن")
CLASS_LINE = re.compile(r"\b(VIP\+?|Class\s+[A-E]|Gold|Silver|Bronze|Platinum|[A-E])\b", re.I)


def extract_rules(text: str, issued: date | None = None) -> QuoteExtraction:
    lines = _lines(text)
    issued = issued or date.today()
    notes: list[str] = []

    if not text.strip():
        return QuoteExtraction(
            outcome="no_quote",
            premium=None,
            currency=None,
            coverage_limit=None,
            deductible=None,
            exclusions=None,
            conditions=None,
            validity_date=None,
            commission_rate=None,
            insurer_reference=None,
            questions_for_broker=None,
            confidence="low",
            notes="الملف بلا نص قابل للقراءة (غالبًا صورة ممسوحة). "
            "أدخل العرض يدويًا أو فعّل الاستخراج بالذكاء الاصطناعي.",
        )

    premium = _labelled_amount(lines, TOTAL)
    if premium is None:
        premium = _labelled_amount(lines, PREMIUM, exclude=("vat", "ضريبه"))
        if premium is not None:
            notes.append("القسط المأخوذ قد لا يشمل الضريبة، فتحقق منه.")
    vat = _labelled_amount(lines, VAT, exclude=TOTAL)
    if vat is not None and premium is not None and vat < premium:
        notes.append(f"الضريبة المذكورة: {vat:,.2f}")
    limit = _labelled_amount(lines, LIMIT)
    deductible = _labelled_text(lines, DEDUCTIBLE)
    commission = None
    for line in lines:
        if _has(line, COMMISSION):
            m = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%|%\s*(\d{1,2}(?:\.\d+)?)", line)
            if m:
                commission = float(m.group(1) or m.group(2))
                break

    validity: date | None = None
    for i, line in enumerate(lines):
        if _has(line, VALID):
            validity = _date(line) or (_date(lines[i + 1]) if i + 1 < len(lines) else None)
            days = re.search(r"(\d{1,3})\s*(days|day|يوم|يومًا|يوما|ايام)", line, re.I)
            if validity is None and days:
                validity = issued + timedelta(days=int(days.group(1)))
                notes.append(f"الصلاحية محسوبة: {days.group(1)} يومًا من اليوم.")
            if validity:
                break

    reference = _labelled_text(lines, REFERENCE)
    if reference:
        reference = re.split(r"\s{2,}", reference)[0][:60]

    network = _labelled_text(lines, NETWORK)
    if network:
        notes.append(f"الشبكة الطبية: {network[:80]}")
    classes = []
    for line in lines:
        m = CLASS_LINE.search(line)
        amounts = [v for v in _amounts(line) if v >= 100]
        if m and amounts and not _has(line, TOTAL + VAT + LIMIT):
            classes.append(f"{m.group(1)}: {max(amounts):,.2f}")
    if 1 < len(classes) <= 8:
        notes.append("أقساط حسب الفئة: " + "، ".join(classes))

    declined = any(_has(line, DECLINE) for line in lines[:40]) and premium is None
    found = sum(x is not None for x in (premium, limit, validity, deductible, reference))
    confidence: Literal["high", "medium", "low"] = (
        "high" if premium is not None and found >= 3 else "medium" if premium is not None else "low"
    )
    if premium is None and not declined:
        notes.append("لم يُعثر على القسط في الملف.")
    return QuoteExtraction(
        outcome="declined" if declined else "quote" if premium is not None else "no_quote",
        premium=premium,
        currency="SAR",
        coverage_limit=limit,
        deductible=deductible,
        exclusions=_labelled_text(lines, EXCLUSIONS),
        conditions=_labelled_text(lines, CONDITIONS),
        validity_date=validity.isoformat() if validity else None,
        commission_rate=commission,
        insurer_reference=reference,
        questions_for_broker=None,
        confidence=confidence,
        notes="\n".join(notes) or None,
    )


def network_class_from(extraction: QuoteExtraction) -> str | None:
    """'Class A' / 'الفئة B' / 'VIP' from the network note, normalized for matching."""
    for line in (extraction.notes or "").splitlines():
        if line.startswith("الشبكة الطبية"):
            m = CLASS_LINE.search(line)
            if m:
                return normalize_class(m.group(1))
    return None


def normalize_class(value: str | None) -> str:
    s = _norm(value or "")
    s = re.sub(r"\b(class|network|tier|plan)\b|الفئه|فئه|الشبكه|شبكه|الدرجه|درجه", " ", s)
    return re.sub(r"\s+", " ", s).strip().upper()
