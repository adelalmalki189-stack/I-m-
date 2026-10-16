"""Files insurers send: quotation PDFs (read into draft quotes) and medical network lists."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import UNDERWRITING, audit, get_current_staff, require_roles
from app.api.deps import get_db_session
from app.automation import inbound
from app.core.config import settings
from app.insurance import aftersales, catalog, documents, networks, quote_pdf, sheets
from app.insurance.models import (
    InsuranceQuote,
    InsuranceRequest,
    InsuranceSubmission,
    InsuranceSubmissionStatus,
    Insurer,
    SalesEmployee,
)
from app.insurance.schemas import InsuranceQuoteResponse

router = APIRouter(prefix="/insurance", tags=["insurer-files"], dependencies=[Depends(get_current_staff)])

INSURER_QUOTE = "insurer_quote"
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_SHEET_BYTES = 15 * 1024 * 1024


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message})


async def _read(file: UploadFile, limit: int) -> bytes:
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise _error(413, "FILE_TOO_LARGE", f"Files must be at most {limit // (1024 * 1024)} MB")
    return content


async def _extract(content: bytes, file_name: str, prompt: str) -> tuple[inbound.QuoteExtraction, str]:
    """Claude when AI extraction is on (falls back to the rules on failure), the rules otherwise."""
    if settings.ai_quote_extraction_enabled:
        try:
            return await inbound.claude_extractor(prompt, [(file_name, "application/pdf", content)]), "ai"
        except Exception:  # network, API, refusal: the rules still give the underwriter a draft
            pass
    try:
        text = quote_pdf.pdf_text(content)
    except quote_pdf.PdfUnreadable as exc:
        raise _error(422, "PDF_UNREADABLE", str(exc)) from exc
    return quote_pdf.extract_rules(text), "rules"


@router.post("/submissions/{submission_id}/quote-pdf", status_code=201)
async def upload_quote_pdf(
    submission_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> dict[str, Any]:
    """Store the insurer's quotation PDF on the request and read it into a draft quote."""
    submission = await session.get(InsuranceSubmission, submission_id)
    if submission is None:
        raise _error(404, "SUBMISSION_NOT_FOUND", "Submission not found")
    content = await _read(file, MAX_PDF_BYTES)
    if not content.startswith(b"%PDF"):
        raise _error(415, "UNSUPPORTED_FILE_TYPE", "Upload the insurer's quotation as a PDF")
    request = await session.get(InsuranceRequest, submission.request_id)
    insurer = await session.get(Insurer, submission.insurer_id)
    assert request is not None
    prompt = (
        f"Our request {request.request_number}: {catalog.line_label(request.insurance_type)} / "
        f"{catalog.product_label(request.insurance_type, request.product_type)}.\n"
        f"Insurer: {insurer.name if insurer else 'unknown'}\n"
        "The attached PDF is the insurer's quotation. Extract the offer. For medical, put the network "
        "class in notes as 'الشبكة الطبية: <class>' and per-class premiums in notes."
    )
    name = (file.filename or "quote.pdf").rsplit("/", 1)[-1][:200]
    result, method = await _extract(content, name, prompt)

    doc = await documents.store_bytes(
        session,
        request.request_id,
        content,
        name,
        "application/pdf",
        INSURER_QUOTE,
        staff.employee_id,
        reviewed=False,
    )
    doc.review_status = "accepted"
    quote = None
    if result.outcome == "declined":
        submission.status = InsuranceSubmissionStatus.REJECTED
        submission.notes = "\n".join(
            filter(None, [submission.notes, f"اعتذار الشركة (من ملف PDF): {result.notes or ''}"])
        )
    elif result.premium is not None:
        notes = [
            "مقروء من ملف PDF " + ("بالذكاء الاصطناعي" if method == "ai" else "بالقواعد الآلية"),
            f"ثقة الاستخراج: {inbound.CONFIDENCE_LABELS[result.confidence]}",
        ]
        if result.insurer_reference:
            notes.append(f"مرجع الشركة: {result.insurer_reference}")
        if result.notes:
            notes.append(result.notes)
        quote = InsuranceQuote(
            submission_id=submission.submission_id,
            premium=str(aftersales.to_money(result.premium)),
            coverage_limit=str(aftersales.to_money(result.coverage_limit)) if result.coverage_limit else None,
            exclusions=result.exclusions,
            conditions=result.conditions,
            deductible=result.deductible,
            validity_date=inbound._validity(result.validity_date),
            currency=(result.currency or "SAR")[:10],
            commission_rate=Decimal(str(result.commission_rate)) if result.commission_rate is not None else None,
            network_class=quote_pdf.network_class_from(result),
            source_channel="pdf_ai" if method == "ai" else "pdf",
            source_document_id=doc.document_id,
            needs_review=True,
            extraction_notes="\n".join(notes),
        )
        session.add(quote)
        await session.flush()
    audit(
        session,
        staff,
        "quote_pdf_uploaded",
        "request",
        request.request_id,
        submission_id=submission_id,
        outcome=result.outcome,
        method=method,
    )
    await session.commit()
    if quote is not None:
        await session.refresh(quote)
    return {
        "document_id": doc.document_id,
        "method": method,
        "extraction": result.model_dump(),
        "quote": InsuranceQuoteResponse.model_validate(quote).model_dump(mode="json") if quote else None,
    }


# --- Medical networks ---------------------------------------------------------------------


async def _insurer(session: AsyncSession, insurer_id: str) -> Insurer:
    insurer = await session.get(Insurer, insurer_id)
    if insurer is None:
        raise _error(404, "INSURER_NOT_FOUND", "Insurer not found")
    return insurer


@router.post("/insurers/{insurer_id}/network", status_code=201)
async def upload_network(
    insurer_id: str,
    file: UploadFile = File(...),
    default_class: str | None = Form(default=None, max_length=40),
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> dict[str, Any]:
    """Replace the insurer's medical network with the providers in this Excel list."""
    await _insurer(session, insurer_id)
    content = await _read(file, MAX_SHEET_BYTES)
    try:
        parsed = networks.parse(content, default_class)
    except sheets.SheetError as exc:
        raise _error(422, "SHEET_UNREADABLE", str(exc)) from exc
    if not parsed.rows:
        raise _error(422, "SHEET_UNREADABLE", "لم أجد مقدمي خدمة في الملف.")
    stored = await networks.replace(session, insurer_id, parsed.rows)
    audit(session, staff, "network_uploaded", "insurer", insurer_id, providers=len(parsed.rows), entries=stored)
    await session.commit()
    return {
        "providers": len(parsed.rows),
        "entries": stored,
        "columns": parsed.columns,
        "class_columns": parsed.class_columns,
        "issues": parsed.issues[:100],
        "summary": await networks.summary(session, insurer_id),
    }


@router.get("/insurers/{insurer_id}/network")
async def network_summary(
    insurer_id: str, network_class: str | None = None, session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    await _insurer(session, insurer_id)
    return await networks.summary(session, insurer_id, network_class)


@router.get("/insurers/{insurer_id}/network/providers")
async def network_providers(
    insurer_id: str,
    network_class: str | None = None,
    q: str | None = None,
    city: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    await _insurer(session, insurer_id)
    return await networks.search(session, insurer_id, network_class, q, city, limit=200)
