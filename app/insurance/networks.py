"""Insurers' medical networks, loaded from their Excel lists of providers.

A list has one row per provider; the class(es) it belongs to come from a class column
("A", "VIP, A, B"), from one column per class marked ✓/Yes, or from a default class given
at upload for single-class lists. A new upload replaces that insurer's network.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.insurance import sheets
from app.insurance.models import NetworkProvider
from app.insurance.quote_pdf import normalize_class

F = sheets.FieldDef
PROVIDERS = sheets.Profile(
    "providers",
    "شبكة مقدمي الخدمة",
    (
        F(
            "name",
            "مقدم الخدمة",
            "Provider",
            (
                "provider name",
                "provider",
                "hospital",
                "hospital name",
                "name",
                "اسم مقدم الخدمه",
                "مقدم الخدمه",
                "اسم المستشفي",
                "المستشفي",
                "الاسم",
                "اسم الجهه",
            ),
            True,
        ),
        F("city", "المدينة", "City", ("city", "المدينه")),
        F(
            "provider_type",
            "النوع",
            "Type",
            ("type", "provider type", "category", "النوع", "نوع مقدم الخدمه", "التصنيف", "نوع الجهه"),
        ),
        F(
            "network_class",
            "الفئة",
            "Class",
            ("class", "network", "network class", "tier", "level", "الفئه", "الشبكه", "الدرجه", "فئه الشبكه"),
        ),
        F("region", "المنطقة", "Region", ("region", "province", "المنطقه")),
        F("phone", "الهاتف", "Phone", ("phone", "tel", "telephone", "الهاتف", "رقم الهاتف")),
    ),
    category_field="network_class",
    category_label="الفئة",
)
CLASS_TOKEN = re.compile(r"^(VIP\+?|[A-E]|GOLD|SILVER|BRONZE|PLATINUM|DIAMOND)$")
YES = {"y", "yes", "نعم", "✓", "✔", "x", "1", "true", "متاح", "مشمول"}
TYPES = {
    "مستشفى": ("hospital", "مستشفي", "مستشفى"),
    "مجمع طبي / عيادة": (
        "clinic",
        "polyclinic",
        "medical center",
        "medical centre",
        "مجمع",
        "مستوصف",
        "عياده",
        "مركز طبي",
    ),
    "صيدلية": ("pharmacy", "صيدليه"),
    "مختبر / أشعة": ("lab", "laboratory", "radiology", "مختبر", "اشعه"),
    "بصريات": ("optical", "optics", "بصريات", "نظارات"),
}
MAX_PROVIDERS = 20_000


def _type(value: str) -> str | None:
    n = sheets.norm(value)
    if not n:
        return None
    for label, words in TYPES.items():
        if any(sheets.norm(w) in n for w in words):
            return label
    return value.strip()[:80]


def split_classes(value: Any) -> list[str]:
    parts = re.split(r"[,،/|;؛+&]| و ", sheets._text(value))
    return [c for c in (normalize_class(p) for p in parts) if c]


@dataclass
class Parsed:
    rows: list[dict[str, Any]]
    issues: list[str] = field(default_factory=list)
    columns: dict[str, str | None] = field(default_factory=dict)
    class_columns: list[str] = field(default_factory=list)


def parse(content: bytes, default_class: str | None = None) -> Parsed:
    sheet = sheets.read_workbook(content)
    mapping = sheets.auto_mapping(PROVIDERS, sheet.headers)
    # One column per class (headers "VIP", "A", "B"...) when there is no single class column.
    class_columns = {
        i: normalize_class(h)
        for i, h in enumerate(sheet.headers)
        if i not in mapping.values() and CLASS_TOKEN.match(normalize_class(h))
    }
    if class_columns and mapping["network_class"] is not None:
        class_columns = {}
    default = normalize_class(default_class) if default_class else None
    if mapping["network_class"] is None and not class_columns and not default:
        raise sheets.SheetError("لم أجد عمود الفئة في الملف. حدّد الفئة التي يخصها هذا الكشف.")

    rows, issues = [], []
    for excel_row, cells in sheet.rows:

        def cell(key: str) -> str:
            i = mapping[key]
            return sheets._text(cells[i]) if i is not None else ""

        name = re.sub(r"\s+", " ", cell("name"))
        if not name:
            issues.append(f"صف {excel_row}: اسم مقدم الخدمة فارغ، تُرك")
            continue
        if class_columns:
            classes = [c for i, c in class_columns.items() if sheets.norm(sheets._text(cells[i])) in YES]
        else:
            classes = (
                split_classes(cells[mapping["network_class"]]) if mapping["network_class"] is not None else []
            )
        classes = classes or ([default] if default else [])
        if not classes:
            issues.append(f"صف {excel_row}: «{name}» بلا فئة، تُرك")
            continue
        rows.append(
            {
                "name": name[:300],
                "city": cell("city")[:120] or None,
                "provider_type": _type(cell("provider_type")),
                "region": cell("region")[:120] or None,
                "phone": cell("phone")[:40] or None,
                "classes": sorted(set(classes)),
            }
        )
    if len(rows) > MAX_PROVIDERS:
        raise sheets.SheetError(f"الكشف أكبر من {MAX_PROVIDERS:,} مقدم خدمة.")
    columns = {
        f.label: (sheet.headers[i] if (i := mapping[f.key]) is not None else None) for f in PROVIDERS.fields
    }
    return Parsed(rows, issues, columns, [sheet.headers[i] for i in class_columns])


async def replace(session: AsyncSession, insurer_id: str, rows: list[dict[str, Any]]) -> int:
    await session.execute(delete(NetworkProvider).where(NetworkProvider.insurer_id == insurer_id))
    count = 0
    for row in rows:
        for network_class in row["classes"]:
            session.add(
                NetworkProvider(
                    insurer_id=insurer_id,
                    network_class=network_class,
                    **{k: row[k] for k in ("name", "city", "provider_type", "region", "phone")},
                )
            )
            count += 1
    await session.flush()
    return count


async def summary(session: AsyncSession, insurer_id: str, network_class: str | None = None) -> dict[str, Any]:
    stmt = select(NetworkProvider).where(NetworkProvider.insurer_id == insurer_id)
    if network_class:
        stmt = stmt.where(NetworkProvider.network_class == normalize_class(network_class))
    providers = (await session.execute(stmt)).scalars().all()
    unique = {(p.name, p.city) for p in providers}
    return {
        "network_class": normalize_class(network_class) if network_class else None,
        "total": len(unique),
        "by_class": dict(Counter(p.network_class for p in providers)) if not network_class else None,
        "by_type": dict(Counter(p.provider_type or "غير محدد" for p in providers).most_common()),
        "top_cities": Counter(p.city or "غير محدد" for p in providers).most_common(8),
        "updated_at": max((p.created_at for p in providers), default=None),
    }


async def search(
    session: AsyncSession,
    insurer_id: str,
    network_class: str | None,
    q: str | None = None,
    city: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    stmt = select(NetworkProvider).where(NetworkProvider.insurer_id == insurer_id)
    if network_class:
        stmt = stmt.where(NetworkProvider.network_class == normalize_class(network_class))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(NetworkProvider.name.ilike(like), NetworkProvider.city.ilike(like)))
    if city:
        stmt = stmt.where(func.lower(NetworkProvider.city) == city.strip().lower())
    rows = (
        await session.execute(stmt.order_by(NetworkProvider.city, NetworkProvider.name).limit(limit))
    ).scalars()
    return [
        {"name": p.name, "city": p.city, "type": p.provider_type, "class": p.network_class, "phone": p.phone}
        for p in rows
    ]
