from __future__ import annotations

import json
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.config import settings
from app.insurance.models import (
    Customer,
    EmailAuditLog,
    Insurer,
    InsurancePolicy,
    InsurancePolicyStatus,
    InsuranceQuote,
    InsuranceRequest,
    InsuranceRequestPreference,
    InsuranceRequestStatus,
    InsuranceSubmission,
    InsuranceSubmissionStatus,
    InsuranceUnderwritingCase,
    InsurerTemplate,
    RequestDocument,
    SalesEmployee,
    UnderwritingAssignment,
)
from app.insurance.schemas import (
    CustomerCreateRequest,
    CustomerResponse,
    InsurancePolicyCreateRequest,
    InsurancePolicyResponse,
    InsurancePolicyStatusUpdateRequest,
    InsuranceQuoteCreateRequest,
    InsuranceQuoteResponse,
    InsuranceRequestCreateRequest,
    InsuranceRequestPreferenceCreateRequest,
    InsuranceRequestPreferenceResponse,
    InsuranceRequestResponse,
    InsuranceRequestStatusUpdateRequest,
    InsuranceSubmissionCreateRequest,
    InsuranceSubmissionResponse,
    InsuranceUnderwritingCaseResponse,
    InsurerCreateRequest,
    InsurerResponse,
    InsurerTemplateCreateRequest,
    InsurerTemplateResponse,
    RequestDocumentResponse,
    SalesEmployeeCreateRequest,
    SalesEmployeeResponse,
    UnderwritingAssignmentRequest,
    UnderwritingAssignmentResponse,
)

router = APIRouter(prefix="/insurance", tags=["insurance"])


def _error(status_code: int, error_code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error_code": error_code, "message": message})


async def _get_customer_or_404(session: AsyncSession, customer_id: str) -> Customer:
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise _error(404, "CUSTOMER_NOT_FOUND", f"No customer found with id '{customer_id}'")
    return customer


async def _get_request_or_404(session: AsyncSession, request_id: str) -> InsuranceRequest:
    request = await session.get(InsuranceRequest, request_id)
    if request is None:
        raise _error(404, "REQUEST_NOT_FOUND", f"No insurance request found with id '{request_id}'")
    return request


async def _get_insurer_or_404(session: AsyncSession, insurer_id: str) -> Insurer:
    insurer = await session.get(Insurer, insurer_id)
    if insurer is None:
        raise _error(404, "INSURER_NOT_FOUND", f"No insurer found with id '{insurer_id}'")
    return insurer


async def _get_employee_or_404(session: AsyncSession, employee_id: str) -> SalesEmployee:
    employee = await session.get(SalesEmployee, employee_id)
    if employee is None:
        raise _error(404, "EMPLOYEE_NOT_FOUND", f"No employee found with id '{employee_id}'")
    return employee


async def _get_submission_or_404(session: AsyncSession, submission_id: str) -> InsuranceSubmission:
    submission = await session.get(InsuranceSubmission, submission_id)
    if submission is None:
        raise _error(404, "SUBMISSION_NOT_FOUND", f"No insurer submission found with id '{submission_id}'")
    return submission



def _render_email_template(template_text: str, context: dict[str, str]) -> str:
    safe_context = {k: str(v) for k, v in context.items()}
    return template_text.format_map({**safe_context})


def _normalize_list(value: list[str] | None) -> list[str]:
    if value is None:
        return []
    return [str(item) for item in value if str(item).strip()]


def _parse_money(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _document_storage_root() -> Path:
    return Path(__file__).resolve().parents[3] / "app" / "static" / "uploads"


def _resolve_document_path(storage_key: str | None) -> Path | None:
    if not storage_key:
        return None
    candidate = Path(storage_key)
    if not candidate.is_absolute():
        candidate = Path(__file__).resolve().parents[3] / candidate
    return candidate if candidate.exists() else None


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _generate_quote_summary_pdf(request: InsuranceRequest, insurer: Insurer, quote: InsuranceQuote | None = None) -> bytes:
    lines = [
        f"Request: {request.request_number}",
        f"Customer: {request.customer_id}",
        f"Type: {request.insurance_type} / {request.product_type}",
        f"Insurer: {insurer.name}",
        f"Coverage: {request.coverage_amount or 'Not specified'}",
    ]
    if quote is not None:
        lines.extend([
            f"Premium: {quote.premium}",
            f"Coverage Limit: {quote.coverage_limit or 'Not specified'}",
            f"Validity: {quote.validity_date.isoformat() if quote.validity_date else 'Not specified'}",
        ])

    text_lines = "\n".join(lines)
    escaped_lines = [f"({ _pdf_escape(line) })" for line in text_lines.splitlines()]
    content = "BT\n/F1 12 Tf\n50 760 Td\n"
    y = 740
    for line in escaped_lines:
        content += f"1 0 0 1 50 {y} Tm\n{line} Tj\n"
        y -= 18
    content += "ET"

    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content.encode('latin-1'))} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    pdf = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{index} 0 obj\n{obj}\nendobj\n".encode("latin-1")
    xref_start = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        pdf += f"{offset:010d} 00000 n \n".encode("latin-1")
    pdf += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode("latin-1")
    return pdf


def _build_insurer_email_message(
    submission: InsuranceSubmission,
    insurer: Insurer,
    request: InsuranceRequest,
    customer: Customer,
    quote: InsuranceQuote | None = None,
    attachment_documents: list[RequestDocument] | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.smtp_from_email
    message["To"] = insurer.email
    message["Subject"] = submission.subject
    body = submission.email_body
    if quote is not None:
        body += (
            "\n\nQuote details:\n"
            f"- Premium: {quote.premium}\n"
            f"- Coverage Limit: {quote.coverage_limit or 'Not specified'}\n"
            f"- Validity: {quote.validity_date.isoformat() if quote.validity_date else 'Not specified'}\n"
            f"- Currency: {quote.currency}\n"
        )
    if customer.full_name:
        body = f"Dear {customer.full_name},\n\n{body}"
    message.set_content(body)

    attachment_documents = attachment_documents or []
    for document in attachment_documents:
        file_path = _resolve_document_path(document.storage_key)
        if file_path is not None and file_path.exists() and file_path.is_file():
            data = file_path.read_bytes()
            if "/" in (document.mime_type or ""):
                maintype, subtype = document.mime_type.split("/", 1)
            else:
                maintype, subtype = "application", "octet-stream"
            message.add_attachment(data, maintype=maintype, subtype=subtype, filename=document.file_name)

    if not attachment_documents:
        pdf_payload = _generate_quote_summary_pdf(request, insurer, quote)
        message.add_attachment(pdf_payload, maintype="application", subtype="pdf", filename=f"{request.request_number}-quote-summary.pdf")

    return message


@router.post("/sales-agents", response_model=SalesEmployeeResponse, status_code=201)
@router.post("/employees", response_model=SalesEmployeeResponse, status_code=201)
async def create_employee(
    body: SalesEmployeeCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> SalesEmployeeResponse:
    existing = await session.execute(select(SalesEmployee).where(SalesEmployee.email == str(body.email)))
    if existing.scalar_one_or_none() is not None:
        raise _error(409, "EMPLOYEE_EXISTS", "An employee with this email already exists")

    employee = SalesEmployee(
        full_name=body.full_name,
        role=body.role,
        email=str(body.email),
        phone=body.phone,
        branch=body.branch,
        manager_id=body.manager_id,
    )
    session.add(employee)
    await session.commit()
    await session.refresh(employee)
    return SalesEmployeeResponse.model_validate(employee)


@router.get("/sales-agents", response_model=list[SalesEmployeeResponse])
@router.get("/employees", response_model=list[SalesEmployeeResponse])
async def list_employees(
    session: AsyncSession = Depends(get_db_session),
) -> list[SalesEmployeeResponse]:
    result = await session.execute(select(SalesEmployee).order_by(SalesEmployee.created_at.desc()))
    return [SalesEmployeeResponse.model_validate(item) for item in result.scalars().all()]


@router.post("/customers", response_model=CustomerResponse, status_code=201)
async def create_customer(
    body: CustomerCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> CustomerResponse:
    if body.email:
        existing = await session.execute(select(Customer).where(Customer.email == str(body.email)))
        if existing.scalar_one_or_none() is not None:
            raise _error(409, "CUSTOMER_EXISTS", "A customer with this email already exists")

    customer = Customer(
        customer_type=body.customer_type,
        full_name=body.full_name,
        company_name=body.company_name,
        email=str(body.email) if body.email else None,
        phone=body.phone,
        notes=body.notes,
    )
    session.add(customer)
    await session.commit()
    await session.refresh(customer)
    return CustomerResponse.model_validate(customer)


@router.get("/customers", response_model=list[CustomerResponse])
async def list_customers(
    q: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[CustomerResponse]:
    stmt = select(Customer).order_by(Customer.created_at.desc())
    if q:
        qn = f"%{q.lower()}%"
        stmt = stmt.where(
            (Customer.full_name.ilike(qn))
            | (Customer.company_name.ilike(qn))
            | (Customer.email.ilike(qn))
            | (Customer.phone.ilike(qn))
        )
    result = await session.execute(stmt)
    return [CustomerResponse.model_validate(customer) for customer in result.scalars().all()]


@router.get("/customers/{customer_id}", response_model=CustomerResponse)
async def get_customer(customer_id: str, session: AsyncSession = Depends(get_db_session)) -> CustomerResponse:
    customer = await _get_customer_or_404(session, customer_id)
    return CustomerResponse.model_validate(customer)


@router.post("/requests", response_model=InsuranceRequestResponse, status_code=201)
async def create_request(
    body: InsuranceRequestCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> InsuranceRequestResponse:
    await _get_customer_or_404(session, body.customer_id)

    sales_agent_id = body.sales_agent_id or body.sales_employee_id
    if sales_agent_id:
        await _get_employee_or_404(session, sales_agent_id)

    request_number = f"IR-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"
    preferred_list = _normalize_list(body.preferred_insurers)
    if body.preferred_insurer:
        preferred_list.insert(0, body.preferred_insurer)

    request = InsuranceRequest(
        customer_id=body.customer_id,
        request_number=request_number,
        insurance_type=body.insurance_type,
        product_type=body.product_type,
        request_source=body.request_source,
        status=InsuranceRequestStatus.NEW,
        sales_employee_id=sales_agent_id,
        preferred_insurers=json.dumps(preferred_list),
        preferred_insurer=body.preferred_insurer,
        preferred_premium_range=body.preferred_premium_range,
        premium_budget=body.premium_budget or body.preferred_premium_range,
        coverage_limit=body.coverage_limit,
        customer_priority=body.customer_priority,
        customer_preferences=body.customer_preferences,
        sales_notes=body.sales_notes or body.notes,
        summary=body.summary,
        notes=body.notes,
        coverage_amount=body.coverage_amount,
    )
    session.add(request)
    await session.commit()
    await session.refresh(request)
    return InsuranceRequestResponse(
        request_id=request.request_id,
        customer_id=request.customer_id,
        request_number=request.request_number,
        insurance_type=request.insurance_type,
        product_type=request.product_type,
        request_source=request.request_source,
        status=request.status,
        sales_agent_id=request.sales_employee_id,
        sales_employee_id=request.sales_employee_id,
        underwriter_id=request.underwriter_id,
        underwriter_status=request.underwriter_status,
        preferred_insurer=request.preferred_insurer,
        preferred_insurers=json.loads(request.preferred_insurers or "[]"),
        preferred_premium_range=request.preferred_premium_range,
        premium_budget=request.premium_budget,
        coverage_limit=request.coverage_limit,
        customer_priority=request.customer_priority,
        customer_preferences=request.customer_preferences,
        sales_notes=request.sales_notes,
        selected_quote_id=request.selected_quote_id,
        coverage_amount=request.coverage_amount,
        summary=request.summary,
        notes=request.notes,
        created_at=request.created_at,
        updated_at=request.updated_at,
        documents=[],
    )


@router.post("/insurers", response_model=InsurerResponse, status_code=201)
async def create_insurer(
    body: InsurerCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> InsurerResponse:
    existing = await session.execute(select(Insurer).where(Insurer.email == str(body.email)))
    if existing.scalar_one_or_none() is not None:
        raise _error(409, "INSURER_EXISTS", "An insurer with this email already exists")

    insurer = Insurer(
        name=body.name,
        email=str(body.email),
        contact_name=body.contact_name,
        communication_channel=body.communication_channel,
        notes=body.notes,
    )
    session.add(insurer)
    await session.commit()
    await session.refresh(insurer)
    return InsurerResponse.model_validate(insurer)


@router.get("/insurers", response_model=list[InsurerResponse])
async def list_insurers(
    session: AsyncSession = Depends(get_db_session),
) -> list[InsurerResponse]:
    result = await session.execute(select(Insurer).order_by(Insurer.created_at.desc()))
    return [InsurerResponse.model_validate(item) for item in result.scalars().all()]


@router.post("/insurers/{insurer_id}/templates", response_model=InsurerTemplateResponse, status_code=201)
async def create_insurer_template(
    insurer_id: str,
    body: InsurerTemplateCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> InsurerTemplateResponse:
    await _get_insurer_or_404(session, insurer_id)

    template = InsurerTemplate(
        insurer_id=insurer_id,
        name=body.name,
        category=body.category,
        subject_template=body.subject_template,
        body_template=body.body_template,
        required_attachments=json.dumps(body.required_attachments),
        active=body.active,
    )
    session.add(template)
    await session.commit()
    await session.refresh(template)
    return InsurerTemplateResponse(
        template_id=template.template_id,
        insurer_id=template.insurer_id,
        name=template.name,
        category=template.category,
        subject_template=template.subject_template,
        body_template=template.body_template,
        required_attachments=json.loads(template.required_attachments or "[]"),
        active=template.active,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


@router.post("/submissions", response_model=InsuranceSubmissionResponse, status_code=201)
async def create_submission(
    body: InsuranceSubmissionCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> InsuranceSubmissionResponse:
    request = await _get_request_or_404(session, body.request_id)
    insurer = await _get_insurer_or_404(session, body.insurer_id)
    customer = await _get_customer_or_404(session, request.customer_id)

    template_result = await session.execute(
        select(InsurerTemplate)
        .where(InsurerTemplate.insurer_id == insurer.insurer_id)
        .where(InsurerTemplate.category == body.category)
        .where(InsurerTemplate.active.is_(True))
        .order_by(InsurerTemplate.created_at.desc())
    )
    template = template_result.scalar_one_or_none()

    if template is None:
        subject = f"Quote request for {request.request_number}"
        email_body = (
            "Hello,\n\nPlease provide a quotation for the request "
            f"{request.request_number} for {request.insurance_type} / {request.product_type}.\n"
            f"Coverage amount: {request.coverage_amount or 'Not specified'}\n"
            f"Summary: {request.summary or 'No summary provided'}\n"
        )
    else:
        subject = _render_email_template(
            template.subject_template,
            {
                "request_number": request.request_number,
                "insurance_type": request.insurance_type,
                "product_type": request.product_type,
                "coverage_amount": request.coverage_amount or "Not specified",
                "customer_name": customer.full_name,
            },
        )
        email_body = _render_email_template(
            template.body_template,
            {
                "request_number": request.request_number,
                "insurance_type": request.insurance_type,
                "product_type": request.product_type,
                "coverage_amount": request.coverage_amount or "Not specified",
                "customer_name": customer.full_name,
            },
        )

    submission = InsuranceSubmission(
        request_id=request.request_id,
        insurer_id=insurer.insurer_id,
        category=body.category,
        subject=subject,
        email_body=email_body,
        communication_channel=insurer.communication_channel or "email",
        status=InsuranceSubmissionStatus.PENDING_APPROVAL,
        notes=body.notes,
    )
    session.add(submission)
    await session.flush()

    audit_entry = EmailAuditLog(
        submission_id=submission.submission_id,
        action="created",
        actor_name="system",
        details=json.dumps({"category": body.category, "request_id": request.request_id, "insurer_id": insurer.insurer_id}),
    )
    session.add(audit_entry)
    await session.commit()
    await session.refresh(submission)
    return InsuranceSubmissionResponse(
        submission_id=submission.submission_id,
        request_id=submission.request_id,
        insurer_id=submission.insurer_id,
        category=submission.category,
        subject=submission.subject,
        email_body=submission.email_body,
        communication_channel=submission.communication_channel,
        status=submission.status,
        approved_for_send=submission.approved_for_send,
        sent_at=submission.sent_at,
        notes=submission.notes,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
    )


@router.get("/submissions", response_model=list[InsuranceSubmissionResponse])
async def list_submissions(
    session: AsyncSession = Depends(get_db_session),
) -> list[InsuranceSubmissionResponse]:
    result = await session.execute(select(InsuranceSubmission).order_by(InsuranceSubmission.created_at.desc()))
    return [
        InsuranceSubmissionResponse(
            submission_id=item.submission_id,
            request_id=item.request_id,
            insurer_id=item.insurer_id,
            category=item.category,
            subject=item.subject,
            email_body=item.email_body,
            communication_channel=item.communication_channel,
            status=item.status,
            approved_for_send=item.approved_for_send,
            sent_at=item.sent_at,
            notes=item.notes,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        for item in result.scalars().all()
    ]


@router.post("/submissions/{submission_id}/quotes", response_model=InsuranceQuoteResponse, status_code=201)
async def create_quote(
    submission_id: str,
    body: InsuranceQuoteCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> InsuranceQuoteResponse:
    submission = await _get_submission_or_404(session, submission_id)
    quote = InsuranceQuote(
        submission_id=submission.submission_id,
        premium=str(body.premium),
        coverage_limit=str(body.coverage_limit) if body.coverage_limit is not None else None,
        exclusions=body.exclusions,
        conditions=body.conditions,
        validity_date=body.validity_date,
        currency=body.currency,
        source_channel=body.source_channel or "email",
    )
    session.add(quote)
    await session.commit()
    await session.refresh(quote)
    return InsuranceQuoteResponse.model_validate(quote)


@router.get("/submissions/{submission_id}/quotes", response_model=list[InsuranceQuoteResponse])
async def list_quotes_for_submission(
    submission_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[InsuranceQuoteResponse]:
    await _get_submission_or_404(session, submission_id)
    result = await session.execute(select(InsuranceQuote).where(InsuranceQuote.submission_id == submission_id))
    return [InsuranceQuoteResponse.model_validate(quote) for quote in result.scalars().all()]


@router.get("/requests/{request_id}/quotes", response_model=list[InsuranceQuoteResponse])
async def list_quotes_for_request(
    request_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[InsuranceQuoteResponse]:
    await _get_request_or_404(session, request_id)
    result = await session.execute(
        select(InsuranceQuote)
        .join(InsuranceSubmission, InsuranceSubmission.submission_id == InsuranceQuote.submission_id)
        .where(InsuranceSubmission.request_id == request_id)
        .order_by(InsuranceQuote.created_at.desc())
    )
    return [InsuranceQuoteResponse.model_validate(quote) for quote in result.scalars().all()]


@router.get("/requests/{request_id}/best-quote", response_model=InsuranceQuoteResponse)
async def get_best_quote_for_request(
    request_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> InsuranceQuoteResponse:
    await _get_request_or_404(session, request_id)
    result = await session.execute(
        select(InsuranceQuote)
        .join(InsuranceSubmission, InsuranceSubmission.submission_id == InsuranceQuote.submission_id)
        .where(InsuranceSubmission.request_id == request_id)
    )
    quotes = result.scalars().all()
    if not quotes:
        raise _error(404, "QUOTES_NOT_FOUND", f"No quotes found for request '{request_id}'")

    best_quote = min(
        quotes,
        key=lambda quote: (
            _parse_money(quote.premium) if _parse_money(quote.premium) is not None else float("inf"),
            -(_parse_money(quote.coverage_limit) if _parse_money(quote.coverage_limit) is not None else 0.0),
        ),
    )
    return InsuranceQuoteResponse.model_validate(best_quote)


@router.post("/requests/{request_id}/quotes/{quote_id}/select", response_model=InsuranceQuoteResponse)
async def select_quote_for_request(
    request_id: str,
    quote_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> InsuranceQuoteResponse:
    request = await _get_request_or_404(session, request_id)
    quote = await session.get(InsuranceQuote, quote_id)
    if quote is None:
        raise _error(404, "QUOTE_NOT_FOUND", f"No quote found with id '{quote_id}'")

    submission = await session.get(InsuranceSubmission, quote.submission_id)
    if submission is None or submission.request_id != request_id:
        raise _error(400, "QUOTE_NOT_FOR_REQUEST", "This quote does not belong to the selected request")

    request.selected_quote_id = quote.quote_id
    request.status = InsuranceRequestStatus.CLOSED
    request.last_status_change_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(request)
    return InsuranceQuoteResponse.model_validate(quote)


@router.get("/requests/{request_id}/selected-quote", response_model=InsuranceQuoteResponse)
async def get_selected_quote_for_request(
    request_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> InsuranceQuoteResponse:
    request = await _get_request_or_404(session, request_id)
    if not request.selected_quote_id:
        raise _error(404, "NO_SELECTED_QUOTE", f"No quote selected for request '{request_id}'")

    quote = await session.get(InsuranceQuote, request.selected_quote_id)
    if quote is None:
        raise _error(404, "SELECTED_QUOTE_NOT_FOUND", f"Selected quote '{request.selected_quote_id}' was not found")
    return InsuranceQuoteResponse.model_validate(quote)


@router.post("/requests/{request_id}/issue-policy", response_model=InsurancePolicyResponse, status_code=201)
async def issue_policy(
    request_id: str,
    body: InsurancePolicyCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> InsurancePolicyResponse:
    request = await _get_request_or_404(session, request_id)
    if not request.selected_quote_id:
        raise _error(400, "NO_SELECTED_QUOTE", f"No quote has been selected for request '{request_id}'")

    quote = await session.get(InsuranceQuote, request.selected_quote_id)
    if quote is None:
        raise _error(404, "SELECTED_QUOTE_NOT_FOUND", f"Selected quote '{request.selected_quote_id}' was not found")

    submission = await session.get(InsuranceSubmission, quote.submission_id)
    if submission is None or submission.request_id != request_id:
        raise _error(400, "QUOTE_NOT_FOR_REQUEST", "The selected quote does not belong to this request")

    existing = await session.execute(select(InsurancePolicy).where(InsurancePolicy.request_id == request_id))
    existing_policy = existing.scalar_one_or_none()
    if existing_policy is not None:
        return InsurancePolicyResponse.model_validate(existing_policy)

    policy_number = f"POL-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"
    effective_from = body.effective_from or datetime.now(UTC)
    effective_to = body.effective_to or effective_from

    policy = InsurancePolicy(
        request_id=request.request_id,
        insurer_id=submission.insurer_id,
        selected_quote_id=quote.quote_id,
        policy_number=policy_number,
        status=InsurancePolicyStatus.ISSUED,
        coverage_amount=request.coverage_amount or quote.coverage_limit,
        premium_amount=quote.premium,
        currency=quote.currency or "SAR",
        effective_from=effective_from,
        effective_to=effective_to,
        issued_at=datetime.now(UTC),
        notes=body.notes,
    )
    session.add(policy)
    request.status = InsuranceRequestStatus.CLOSED
    request.last_status_change_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(policy)
    return InsurancePolicyResponse.model_validate(policy)


@router.get("/policies", response_model=list[InsurancePolicyResponse])
async def list_policies(
    session: AsyncSession = Depends(get_db_session),
) -> list[InsurancePolicyResponse]:
    result = await session.execute(select(InsurancePolicy).order_by(InsurancePolicy.created_at.desc()))
    return [InsurancePolicyResponse.model_validate(policy) for policy in result.scalars().all()]


@router.get("/policies/{policy_id}", response_model=InsurancePolicyResponse)
async def get_policy(policy_id: str, session: AsyncSession = Depends(get_db_session)) -> InsurancePolicyResponse:
    policy = await session.get(InsurancePolicy, policy_id)
    if policy is None:
        raise _error(404, "POLICY_NOT_FOUND", f"No policy found with id '{policy_id}'")
    return InsurancePolicyResponse.model_validate(policy)


@router.get("/requests/{request_id}/policies", response_model=list[InsurancePolicyResponse])
async def list_policies_for_request(
    request_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[InsurancePolicyResponse]:
    await _get_request_or_404(session, request_id)
    result = await session.execute(select(InsurancePolicy).where(InsurancePolicy.request_id == request_id))
    return [InsurancePolicyResponse.model_validate(policy) for policy in result.scalars().all()]


@router.get("/requests/{request_id}/policy", response_model=InsurancePolicyResponse)
async def get_policy_for_request(
    request_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> InsurancePolicyResponse:
    request = await _get_request_or_404(session, request_id)
    result = await session.execute(select(InsurancePolicy).where(InsurancePolicy.request_id == request_id))
    policy = result.scalar_one_or_none()
    if policy is None:
        raise _error(404, "POLICY_NOT_FOUND", f"No policy found for request '{request_id}'")
    return InsurancePolicyResponse.model_validate(policy)


@router.post("/policies/{policy_id}/activate", response_model=InsurancePolicyResponse)
async def activate_policy(
    policy_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> InsurancePolicyResponse:
    policy = await session.get(InsurancePolicy, policy_id)
    if policy is None:
        raise _error(404, "POLICY_NOT_FOUND", f"No policy found with id '{policy_id}'")

    policy.status = InsurancePolicyStatus.ACTIVE
    policy.issued_at = policy.issued_at or datetime.now(UTC)
    await session.commit()
    await session.refresh(policy)
    return InsurancePolicyResponse.model_validate(policy)


@router.patch("/policies/{policy_id}/status", response_model=InsurancePolicyResponse)
async def update_policy_status(
    policy_id: str,
    body: InsurancePolicyStatusUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> InsurancePolicyResponse:
    policy = await session.get(InsurancePolicy, policy_id)
    if policy is None:
        raise _error(404, "POLICY_NOT_FOUND", f"No policy found with id '{policy_id}'")

    policy.status = body.status
    if body.notes:
        policy.notes = body.notes
    await session.commit()
    await session.refresh(policy)
    return InsurancePolicyResponse.model_validate(policy)


@router.post("/submissions/{submission_id}/approve", response_model=InsuranceSubmissionResponse)
async def approve_submission(
    submission_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> InsuranceSubmissionResponse:
    submission = await _get_submission_or_404(session, submission_id)
    submission.approved_for_send = True
    submission.status = InsuranceSubmissionStatus.APPROVED
    session.add(
        EmailAuditLog(
            submission_id=submission.submission_id,
            action="approved",
            actor_name="user",
            details=json.dumps({"approved_for_send": True}),
        )
    )
    await session.commit()
    await session.refresh(submission)
    return InsuranceSubmissionResponse(
        submission_id=submission.submission_id,
        request_id=submission.request_id,
        insurer_id=submission.insurer_id,
        category=submission.category,
        subject=submission.subject,
        email_body=submission.email_body,
        communication_channel=submission.communication_channel,
        status=submission.status,
        approved_for_send=submission.approved_for_send,
        sent_at=submission.sent_at,
        notes=submission.notes,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
    )


@router.post("/submissions/{submission_id}/send", response_model=InsuranceSubmissionResponse)
async def send_submission(
    submission_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> InsuranceSubmissionResponse:
    submission = await _get_submission_or_404(session, submission_id)
    if not submission.approved_for_send:
        raise _error(400, "SUBMISSION_NOT_APPROVED", "This submission must be approved before sending")

    insurer = await _get_insurer_or_404(session, submission.insurer_id)
    request = await _get_request_or_404(session, submission.request_id)
    customer = await _get_customer_or_404(session, request.customer_id)
    quote_result = await session.execute(
        select(InsuranceQuote)
        .where(InsuranceQuote.submission_id == submission.submission_id)
        .order_by(InsuranceQuote.created_at.desc())
    )
    quote = quote_result.scalar_one_or_none()

    documents_result = await session.execute(select(RequestDocument).where(RequestDocument.request_id == request.request_id))
    attachment_documents = documents_result.scalars().all()
    message = _build_insurer_email_message(
        submission,
        insurer,
        request,
        customer,
        quote,
        attachment_documents=attachment_documents,
    )
    delivery_ok = False
    if settings.email_delivery_enabled:
        with smtplib.SMTP(settings.smtp_host, int(settings.smtp_port)) as smtp_client:
            if settings.smtp_use_tls:
                smtp_client.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp_client.login(settings.smtp_username, settings.smtp_password)
            smtp_client.send_message(message)
        delivery_ok = True

    submission.status = InsuranceSubmissionStatus.SENT
    submission.sent_at = datetime.now(UTC)
    session.add(
        EmailAuditLog(
            submission_id=submission.submission_id,
            action="sent",
            actor_name="user",
            details=json.dumps({
                "sent_at": submission.sent_at.isoformat(),
                "delivery_ok": delivery_ok,
                "communication_channel": submission.communication_channel,
            }),
        )
    )
    await session.commit()
    await session.refresh(submission)
    return InsuranceSubmissionResponse(
        submission_id=submission.submission_id,
        request_id=submission.request_id,
        insurer_id=submission.insurer_id,
        category=submission.category,
        subject=submission.subject,
        email_body=submission.email_body,
        communication_channel=submission.communication_channel,
        status=submission.status,
        approved_for_send=submission.approved_for_send,
        sent_at=submission.sent_at,
        notes=submission.notes,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
    )


@router.get("/requests", response_model=list[InsuranceRequestResponse])
async def list_requests(
    customer_id: str | None = None,
    status: InsuranceRequestStatus | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[InsuranceRequestResponse]:
    stmt = select(InsuranceRequest).order_by(InsuranceRequest.created_at.desc())
    if customer_id:
        stmt = stmt.where(InsuranceRequest.customer_id == customer_id)
    if status:
        stmt = stmt.where(InsuranceRequest.status == status)

    result = await session.execute(stmt)
    items = result.scalars().all()
    responses: list[InsuranceRequestResponse] = []
    for item in items:
        docs = await session.execute(select(RequestDocument).where(RequestDocument.request_id == item.request_id))
        responses.append(
            InsuranceRequestResponse(
                request_id=item.request_id,
                customer_id=item.customer_id,
                request_number=item.request_number,
                insurance_type=item.insurance_type,
                product_type=item.product_type,
                request_source=item.request_source,
                status=item.status,
                coverage_amount=item.coverage_amount,
                summary=item.summary,
                notes=item.notes,
                created_at=item.created_at,
                updated_at=item.updated_at,
                documents=[
                    RequestDocumentResponse.model_validate(doc)
                    for doc in docs.scalars().all()
                ],
            )
        )
    return responses


@router.get("/requests/{request_id}", response_model=InsuranceRequestResponse)
async def get_request(request_id: str, session: AsyncSession = Depends(get_db_session)) -> InsuranceRequestResponse:
    request = await _get_request_or_404(session, request_id)
    result = await session.execute(select(RequestDocument).where(RequestDocument.request_id == request_id))
    return InsuranceRequestResponse(
        request_id=request.request_id,
        customer_id=request.customer_id,
        request_number=request.request_number,
        insurance_type=request.insurance_type,
        product_type=request.product_type,
        request_source=request.request_source,
        status=request.status,
        coverage_amount=request.coverage_amount,
        summary=request.summary,
        notes=request.notes,
        created_at=request.created_at,
        updated_at=request.updated_at,
        documents=[RequestDocumentResponse.model_validate(doc) for doc in result.scalars().all()],
    )


@router.post("/requests/{request_id}/documents", response_model=RequestDocumentResponse, status_code=201)
async def upload_document(
    request_id: str,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    uploaded_by_employee_id: str | None = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> RequestDocumentResponse:
    await _get_request_or_404(session, request_id)
    if uploaded_by_employee_id:
        await _get_employee_or_404(session, uploaded_by_employee_id)
    if not file.filename:
        raise _error(400, "INVALID_FILE", "A file name is required")

    content = await file.read()
    storage_root = _document_storage_root() / request_id
    storage_root.mkdir(parents=True, exist_ok=True)
    storage_name = f"{uuid4().hex}-{file.filename}"
    storage_path = storage_root / storage_name
    storage_path.write_bytes(content)

    storage_key = str(storage_path.relative_to(Path(__file__).resolve().parents[3]))
    document = RequestDocument(
        request_id=request_id,
        document_type=document_type,
        file_name=file.filename,
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=len(content),
        storage_key=storage_key,
        uploaded_by_employee_id=uploaded_by_employee_id,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return RequestDocumentResponse.model_validate(document)


@router.post("/requests/{request_id}/preferences", response_model=InsuranceRequestPreferenceResponse, status_code=201)
async def create_request_preferences(
    request_id: str,
    body: InsuranceRequestPreferenceCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> InsuranceRequestPreferenceResponse:
    await _get_request_or_404(session, request_id)
    await _get_employee_or_404(session, body.sales_employee_id)

    request = await _get_request_or_404(session, request_id)
    preference = InsuranceRequestPreference(
        request_id=request_id,
        sales_employee_id=body.sales_employee_id,
        preferred_insurers=json.dumps(_normalize_list(body.preferred_insurers)),
        preferred_premium_range=body.preferred_premium_range,
        preferred_coverage_limit=body.preferred_coverage_limit,
        customer_priority=body.customer_priority,
        notes=body.notes,
    )
    session.add(preference)
    request.preferred_insurers = json.dumps(_normalize_list(body.preferred_insurers))
    request.preferred_premium_range = body.preferred_premium_range
    request.customer_priority = body.customer_priority
    request.notes = body.notes
    await session.commit()
    await session.refresh(preference)
    return InsuranceRequestPreferenceResponse(
        preference_id=preference.preference_id,
        request_id=preference.request_id,
        sales_employee_id=preference.sales_employee_id,
        preferred_insurers=json.loads(preference.preferred_insurers or "[]"),
        preferred_premium_range=preference.preferred_premium_range,
        preferred_coverage_limit=preference.preferred_coverage_limit,
        customer_priority=preference.customer_priority,
        notes=preference.notes,
        created_at=preference.created_at,
        updated_at=preference.updated_at,
    )


@router.post("/requests/{request_id}/assign-underwriting", response_model=UnderwritingAssignmentResponse)
async def assign_request_to_underwriting(
    request_id: str,
    body: UnderwritingAssignmentRequest,
    session: AsyncSession = Depends(get_db_session),
):
    request = await _get_request_or_404(session, request_id)
    underwriter_id = body.underwriter_id or body.assigned_underwriter_id
    if not underwriter_id:
        raise _error(400, "UNDERWRITER_ID_REQUIRED", "An underwriter_id is required")
    underwriter = await _get_employee_or_404(session, underwriter_id)

    request.underwriter_id = underwriter.employee_id
    request.underwriter_status = body.underwriting_status or "under_review"
    request.status = InsuranceRequestStatus.UNDER_REVIEW
    request.last_status_change_at = datetime.now(UTC)

    assignment = UnderwritingAssignment(
        request_id=request.request_id,
        assigned_by_employee_id=body.sales_employee_id or request.sales_employee_id,
        assigned_to_employee_id=underwriter.employee_id,
        status="assigned",
        notes=body.notes,
    )
    session.add(assignment)
    await session.commit()
    await session.refresh(assignment)

    response_payload = UnderwritingAssignmentResponse(
        assignment_id=assignment.assignment_id,
        request_id=assignment.request_id,
        assigned_by_employee_id=assignment.assigned_by_employee_id,
        assigned_to_employee_id=assignment.assigned_to_employee_id,
        assigned_underwriter_id=assignment.assigned_to_employee_id,
        status=assignment.status,
        underwriting_status=request.underwriter_status,
        notes=assignment.notes,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )
    status_code = 201 if body.sales_employee_id and body.assigned_underwriter_id else 200
    return JSONResponse(content=response_payload.model_dump(mode="json"), status_code=status_code)


@router.get("/requests/{request_id}/underwriting", response_model=InsuranceUnderwritingCaseResponse)
async def get_underwriting_case(
    request_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> InsuranceUnderwritingCaseResponse:
    request = await _get_request_or_404(session, request_id)
    if not request.underwriter_id:
        raise _error(404, "UNDERWRITING_CASE_NOT_FOUND", f"No underwriting assignment found for request '{request_id}'")

    return InsuranceUnderwritingCaseResponse(
        case_id=request.request_id,
        request_id=request.request_id,
        sales_employee_id=request.sales_employee_id,
        assigned_underwriter_id=request.underwriter_id,
        underwriting_status=request.underwriter_status,
        notes=request.notes,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


@router.patch("/requests/{request_id}/status", response_model=InsuranceRequestResponse)
async def update_request_status(
    request_id: str,
    body: InsuranceRequestStatusUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> InsuranceRequestResponse:
    request = await _get_request_or_404(session, request_id)
    request.status = body.status
    if body.notes:
        request.notes = body.notes
    request.last_status_change_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(request)

    result = await session.execute(select(RequestDocument).where(RequestDocument.request_id == request_id))
    return InsuranceRequestResponse(
        request_id=request.request_id,
        customer_id=request.customer_id,
        request_number=request.request_number,
        insurance_type=request.insurance_type,
        product_type=request.product_type,
        request_source=request.request_source,
        status=request.status,
        coverage_amount=request.coverage_amount,
        summary=request.summary,
        notes=request.notes,
        created_at=request.created_at,
        updated_at=request.updated_at,
        documents=[RequestDocumentResponse.model_validate(doc) for doc in result.scalars().all()],
    )
