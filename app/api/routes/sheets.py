"""Underwriting review of Excel lists: analyze, decide classes and target insurers, format."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import UNDERWRITING, audit, get_current_staff, require_roles
from app.api.deps import get_db_session
from app.insurance import catalog, documents, review, sheets
from app.insurance.models import Customer, InsuranceRequest, Insurer, ListAnalysis, RequestDocument, SalesEmployee

router = APIRouter(prefix="/insurance", tags=["underwriting-lists"], dependencies=[Depends(get_current_staff)])

FORMATTED = "formatted_list"


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message})


def is_sheet(doc: RequestDocument) -> bool:
    return doc.mime_type == review.XLSX_MIME or doc.file_name.lower().endswith(".xlsx")


class AnalysisOptions(BaseModel):
    profile: str | None = Field(default=None, pattern="^(people|vehicles|generic)$")
    sheet_name: str | None = Field(default=None, max_length=100)
    column_mapping: dict[str, int | None] | None = None  # None = detect automatically
    class_mapping: dict[str, str] = Field(default_factory=dict)
    default_class: str | None = Field(default=None, max_length=40)
    include_classes: list[str] | None = None
    exclude_invalid: bool = False
    split_by_class: bool = False
    target_insurer_ids: list[str] = Field(default_factory=list)


async def _file(doc: RequestDocument) -> bytes:
    path = documents.resolve_path(doc.storage_key)
    if path is None:
        raise _error(404, "DOCUMENT_FILE_MISSING", "The stored file is missing")
    return path.read_bytes()


async def _run(
    session: AsyncSession, doc: RequestDocument, opts: AnalysisOptions
) -> tuple[sheets.Profile, sheets.Sheet, AnalysisOptions, sheets.Result]:
    profile = sheets.PROFILES[opts.profile] if opts.profile else sheets.profile_for(doc.document_type)
    try:
        sheet = sheets.read_workbook(await _file(doc), opts.sheet_name)
    except sheets.SheetError as exc:
        raise _error(422, "SHEET_UNREADABLE", str(exc)) from exc
    keys = {f.key for f in profile.fields}
    mapping = sheets.auto_mapping(profile, sheet.headers)
    if opts.column_mapping is not None:
        unknown = set(opts.column_mapping) - keys
        out_of_range = [
            i for i in opts.column_mapping.values() if i is not None and not 0 <= i < len(sheet.headers)
        ]
        if unknown or out_of_range:
            raise _error(422, "INVALID_MAPPING", "Column mapping refers to unknown fields or columns")
        mapping = {k: opts.column_mapping.get(k) for k in keys}
    opts = opts.model_copy(
        update={"profile": profile.key, "sheet_name": sheet.sheet_name, "column_mapping": mapping}
    )
    result = sheets.analyze(
        profile,
        sheet,
        sheets.Options(
            column_mapping=mapping,
            class_mapping={k: v.strip() for k, v in opts.class_mapping.items() if v and v.strip()},
            default_class=(opts.default_class or "").strip() or None,
            include_classes=opts.include_classes,
            exclude_invalid=opts.exclude_invalid,
        ),
    )
    return profile, sheet, opts, result


def _view(
    analysis: ListAnalysis,
    doc: RequestDocument,
    profile: sheets.Profile,
    sheet: sheets.Sheet,
    opts: AnalysisOptions,
    result: sheets.Result,
) -> dict[str, Any]:
    errors = sum(1 for i in result.issues if i["level"] == "error")
    return {
        "analysis_id": analysis.analysis_id,
        "document_id": doc.document_id,
        "file_name": doc.file_name,
        "document_label": catalog.document_label(doc.document_type),
        "profile": profile.key,
        "profile_label": profile.label,
        "category_field": profile.category_field,
        "category_label": profile.category_label,
        "default_categories": list(profile.default_categories),
        "sheet_names": sheet.sheet_names,
        "sheet_name": sheet.sheet_name,
        "header_row": sheet.header_row,
        "headers": sheet.headers,
        "fields": [{"key": f.key, "label": f.label, "required": f.required} for f in profile.fields],
        "options": opts.model_dump(),
        "categories": result.categories,
        "summary": result.summary,
        "issues": result.issues[:300],
        "issue_counts": {"error": errors, "warning": len(result.issues) - errors},
        "preview": [{k: v for k, v in r.items()} for r in result.rows[:8]],
        "truncated": sheet.truncated,
        "status": analysis.status,
        "output_document_id": analysis.output_document_id,
        "finalized_at": analysis.finalized_at,
    }


async def _doc(session: AsyncSession, document_id: str) -> RequestDocument:
    doc = await session.get(RequestDocument, document_id)
    if doc is None:
        raise _error(404, "DOCUMENT_NOT_FOUND", "Document not found")
    if not is_sheet(doc):
        raise _error(422, "NOT_A_SPREADSHEET", "Only Excel (.xlsx) lists can be analyzed")
    return doc


async def _check_insurers(session: AsyncSession, ids: list[str]) -> list[str]:
    clean = list(dict.fromkeys(ids))
    for insurer_id in clean:
        insurer = await session.get(Insurer, insurer_id)
        if insurer is None or not insurer.is_active:
            raise _error(422, "INSURER_INACTIVE", "A selected insurer does not exist or is inactive")
    return clean


@router.get("/requests/{request_id}/lists")
async def request_lists(request_id: str, session: AsyncSession = Depends(get_db_session)) -> list[dict[str, Any]]:
    """The request's Excel lists and the state of their analysis."""
    docs = (
        (
            await session.execute(
                select(RequestDocument)
                .where(RequestDocument.request_id == request_id, RequestDocument.document_type != FORMATTED)
                .order_by(RequestDocument.created_at)
            )
        )
        .scalars()
        .all()
    )
    analyses = {
        a.document_id: a
        for a in (
            await session.execute(select(ListAnalysis).where(ListAnalysis.request_id == request_id))
        ).scalars()
    }
    out = []
    for doc in docs:
        if not is_sheet(doc):
            continue
        a = analyses.get(doc.document_id)
        out.append(
            {
                "document_id": doc.document_id,
                "file_name": doc.file_name,
                "document_type": doc.document_type,
                "document_label": catalog.document_label(doc.document_type),
                "created_at": doc.created_at,
                "analysis": {
                    "analysis_id": a.analysis_id,
                    "status": a.status,
                    "issue_count": a.issue_count,
                    "output_document_id": a.output_document_id,
                    "updated_at": a.updated_at,
                }
                if a
                else None,
            }
        )
    return out


@router.post("/documents/{document_id}/analysis")
async def analyze_document(
    document_id: str,
    body: AnalysisOptions | None = None,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> dict[str, Any]:
    """Analyze (or re-analyze with the underwriter's corrections) and save the decisions."""
    doc = await _doc(session, document_id)
    opts = body or AnalysisOptions()
    opts.target_insurer_ids = await _check_insurers(session, opts.target_insurer_ids)
    analysis = (
        await session.execute(select(ListAnalysis).where(ListAnalysis.document_id == document_id))
    ).scalar_one_or_none()
    if analysis is not None and body is None:
        opts = AnalysisOptions(**json.loads(analysis.options))  # reopen with the saved decisions
    profile, sheet, opts, result = await _run(session, doc, opts)
    if analysis is None:
        analysis = ListAnalysis(request_id=doc.request_id, document_id=doc.document_id, profile=profile.key)
        session.add(analysis)
    elif body is not None:
        analysis.status = "draft"  # changed decisions: the formatted file must be produced again
    analysis.profile = profile.key
    analysis.sheet_name = sheet.sheet_name
    analysis.options = opts.model_dump_json()
    analysis.summary = json.dumps(result.summary, ensure_ascii=False, default=str)
    analysis.issue_count = len(result.issues)
    analysis.analyzed_by_id = staff.employee_id
    audit(
        session, staff, "list_analyzed", "request", doc.request_id, document_id=document_id, rows=len(sheet.rows)
    )
    await session.commit()
    await session.refresh(analysis)
    return _view(analysis, doc, profile, sheet, opts, result)


@router.post("/analyses/{analysis_id}/finalize")
async def finalize_analysis(
    analysis_id: str,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> dict[str, Any]:
    """Write the formatted workbook into the request's files. It replaces the raw list in
    insurer emails; an earlier formatted copy is retired."""
    analysis = await session.get(ListAnalysis, analysis_id)
    if analysis is None:
        raise _error(404, "ANALYSIS_NOT_FOUND", "Analysis not found")
    doc = await _doc(session, analysis.document_id)
    opts = AnalysisOptions(**json.loads(analysis.options))
    profile, sheet, opts, result = await _run(session, doc, opts)
    if not result.rows:
        raise _error(409, "NOTHING_TO_EXPORT", "No rows are left with the chosen classes")
    request = await session.get(InsuranceRequest, analysis.request_id)
    assert request is not None
    customer = await session.get(Customer, request.customer_id)
    insurers = [i for i in [await session.get(Insurer, x) for x in opts.target_insurer_ids] if i]
    title = {
        "customer": (customer.company_name or customer.full_name) if customer else "",
        "رقم الطلب": request.request_number,
        "المنتج": f"{catalog.line_label(request.insurance_type)} - "
        f"{catalog.product_label(request.insurance_type, request.product_type)}",
        "الفئات": "، ".join(c["category"] for c in result.summary["by_category"]),
        "الشركات المستهدفة": "، ".join(i.name for i in insurers) or "—",
        "أعدّه": f"{staff.full_name} - بُعد للوساطة التأمينية",
        "التاريخ": datetime.now(UTC).date().isoformat(),
    }
    content = sheets.build_workbook(profile, result, title, opts.split_by_class)
    if analysis.output_document_id:
        old = await session.get(RequestDocument, analysis.output_document_id)
        if old is not None:
            old.review_status, old.rejection_reason = review.REJECTED, "استُبدل بنسخة منسّقة أحدث"
    output = await documents.store_bytes(
        session,
        request.request_id,
        content,
        f"{request.request_number}-{profile.key}-BUD.xlsx",
        review.XLSX_MIME,
        FORMATTED,
        uploader_id=staff.employee_id,
        reviewed=False,
    )
    output.review_status, output.is_verified = review.ACCEPTED, True
    analysis.output_document_id = output.document_id
    analysis.status = "finalized"
    analysis.finalized_at = datetime.now(UTC)
    audit(session, staff, "list_formatted", "request", request.request_id, document_id=output.document_id)
    await session.commit()
    await session.refresh(analysis)
    return _view(analysis, doc, profile, sheet, opts, result)


async def superseded_lists(session: AsyncSession, request_id: str) -> set[str]:
    """Raw lists that have a finalized formatted copy: insurers get the formatted one only."""
    rows = await session.execute(
        select(ListAnalysis.document_id).where(
            ListAnalysis.request_id == request_id, ListAnalysis.status == "finalized"
        )
    )
    return set(rows.scalars())
