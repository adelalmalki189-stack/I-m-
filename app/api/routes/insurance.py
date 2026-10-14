from __future__ import annotations

import hmac
import json
import smtplib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import MANAGERS, UNDERWRITING, Role, audit, client_ip, get_current_staff, require_roles
from app.api.deps import get_db_session
from app.core.config import settings
from app.core.security import RateLimiter, hash_password, hash_token, new_token, password_problem
from app.insurance import catalog, workflow
from app.insurance.models import (
    AuditEvent,
    Customer,
    EmailAuditLog,
    InsurancePolicy,
    InsurancePolicyStatus,
    InsuranceQuote,
    InsuranceRequest,
    InsuranceRequestPreference,
    InsuranceRequestStatus,
    InsuranceSubmission,
    InsuranceSubmissionStatus,
    Insurer,
    InsurerTemplate,
    RequestDocument,
    SalesEmployee,
    StaffSession,
    UnderwritingAssignment,
)
from app.insurance.schemas import (
    AuditEventResponse,
    BulkSubmissionCreateRequest,
    CustomerCreateRequest,
    CustomerResponse,
    DefaultTemplateCreateRequest,
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
    InsuranceSubmissionUpdateRequest,
    InsuranceUnderwritingCaseResponse,
    InsurerCreateRequest,
    InsurerResponse,
    InsurerTemplateCreateRequest,
    InsurerTemplateResponse,
    InsurerTemplateUpdateRequest,
    InsurerUpdateRequest,
    PasswordSetRequest,
    PortalDocumentResponse,
    PortalRequestCreate,
    PortalRequestResponse,
    RequestDocumentResponse,
    SalesEmployeeCreateRequest,
    SalesEmployeeResponse,
    SalesEmployeeUpdateRequest,
    SubmissionMarkSentRequest,
    TemplatePreviewRequest,
    TemplatePreviewResponse,
    UnderwritingAssignmentRequest,
    UnderwritingAssignmentResponse,
)

# Staff API: every endpoint needs a logged-in employee; some add a role requirement.
router = APIRouter(prefix="/insurance", tags=["insurance"], dependencies=[Depends(get_current_staff)])
# Customer-facing endpoints used by the public portal.
public_router = APIRouter(prefix="/insurance", tags=["portal"])
T = TypeVar("T")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CUSTOMER_TYPE_LABELS = {"individual": "فرد", "business": "منشأة"}


def _error(status_code: int, error_code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error_code": error_code, "message": message})


async def _get_or_404(session: AsyncSession, model: type[T], key: str, code: str, label: str) -> T:
    item = await session.get(model, key)
    if item is None:
        raise _error(404, code, f"No {label} found with id '{key}'")
    return item


async def _get_customer_or_404(session: AsyncSession, customer_id: str) -> Customer:
    return await _get_or_404(session, Customer, customer_id, "CUSTOMER_NOT_FOUND", "customer")


async def _get_request_or_404(session: AsyncSession, request_id: str) -> InsuranceRequest:
    return await _get_or_404(session, InsuranceRequest, request_id, "REQUEST_NOT_FOUND", "insurance request")


async def _get_insurer_or_404(session: AsyncSession, insurer_id: str) -> Insurer:
    return await _get_or_404(session, Insurer, insurer_id, "INSURER_NOT_FOUND", "insurer")


async def _get_employee_or_404(session: AsyncSession, employee_id: str) -> SalesEmployee:
    return await _get_or_404(session, SalesEmployee, employee_id, "EMPLOYEE_NOT_FOUND", "employee")


async def _get_submission_or_404(session: AsyncSession, submission_id: str) -> InsuranceSubmission:
    return await _get_or_404(
        session, InsuranceSubmission, submission_id, "SUBMISSION_NOT_FOUND", "insurer submission"
    )


async def _get_template_or_404(session: AsyncSession, template_id: str) -> InsurerTemplate:
    return await _get_or_404(session, InsurerTemplate, template_id, "TEMPLATE_NOT_FOUND", "template")


def _transition(request: InsuranceRequest, target: InsuranceRequestStatus) -> None:
    try:
        workflow.transition(request, target)
    except workflow.InvalidTransition as exc:
        raise _error(409, "INVALID_STATUS_TRANSITION", str(exc)) from exc


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


def _details(request: InsuranceRequest) -> dict:
    try:
        return json.loads(request.request_details or "{}")
    except json.JSONDecodeError:
        return {}


def _document_storage_root() -> Path:
    return PROJECT_ROOT / "app" / "static" / "uploads"


PORTAL_UPLOAD_WINDOW = timedelta(hours=2)
PORTAL_MAX_DOCUMENTS = 10
STAFF_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Public uploads must really be PDF/JPEG/PNG — checked by the file's first bytes, not its name.
PORTAL_FILE_SIGNATURES = {
    b"%PDF": "application/pdf",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}
portal_limiter = RateLimiter(settings.portal_requests_per_hour, 3600)


async def _store_document(
    session: AsyncSession,
    request_id: str,
    file: UploadFile,
    document_type: str,
    uploader_id: str | None,
    max_bytes: int,
    allowed: dict[bytes, str] | None = None,
) -> RequestDocument:
    # Keep only the final path component so a crafted name like "../../x" can't escape the uploads folder.
    file_name = Path(file.filename or "").name
    if not file_name:
        raise _error(400, "INVALID_FILE", "A file name is required")
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise _error(413, "FILE_TOO_LARGE", f"Files must be at most {max_bytes // (1024 * 1024)} MB")
    mime_type = file.content_type or "application/octet-stream"
    if allowed is not None:
        detected = next((mime for magic, mime in allowed.items() if content.startswith(magic)), None)
        if detected is None:
            raise _error(415, "UNSUPPORTED_FILE_TYPE", "Only PDF, JPEG and PNG files are accepted")
        mime_type = detected

    storage_root = _document_storage_root() / request_id
    storage_root.mkdir(parents=True, exist_ok=True)
    storage_path = storage_root / f"{uuid4().hex}-{file_name}"
    storage_path.write_bytes(content)
    document = RequestDocument(
        request_id=request_id,
        document_type=document_type,
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=len(content),
        storage_key=str(storage_path.relative_to(PROJECT_ROOT)),
        uploaded_by_employee_id=uploader_id,
    )
    session.add(document)
    await session.flush()
    return document


def _resolve_document_path(storage_key: str | None) -> Path | None:
    if not storage_key:
        return None
    candidate = Path(storage_key)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate if candidate.exists() else None


# --- Response builders -------------------------------------------------------


async def _request_response(session: AsyncSession, request: InsuranceRequest) -> InsuranceRequestResponse:
    docs = await session.execute(select(RequestDocument).where(RequestDocument.request_id == request.request_id))
    customer = await session.get(Customer, request.customer_id)
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
        customer_name=(customer.company_name or customer.full_name) if customer else None,
        insurance_type_label=catalog.line_label(request.insurance_type),
        product_type_label=catalog.product_label(request.insurance_type, request.product_type),
        request_details=_details(request),
        allowed_transitions=workflow.allowed_next(request.status),
        last_status_change_at=request.last_status_change_at,
        created_at=request.created_at,
        updated_at=request.updated_at,
        documents=[RequestDocumentResponse.model_validate(doc) for doc in docs.scalars().all()],
    )


def _submission_response(submission: InsuranceSubmission) -> InsuranceSubmissionResponse:
    return InsuranceSubmissionResponse.model_validate(submission)


def _template_response(template: InsurerTemplate) -> InsurerTemplateResponse:
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


def _email_audit(
    session: AsyncSession, submission: InsuranceSubmission, action: str, actor: str, **details
) -> None:
    session.add(
        EmailAuditLog(
            submission_id=submission.submission_id,
            action=action,
            actor_name=actor,
            details=json.dumps(details, ensure_ascii=False, default=str),
        )
    )


# --- Catalog & templates -----------------------------------------------------


@public_router.get("/catalog")
async def get_catalog() -> dict:
    """Every insurance line with its products and required risk-detail fields."""
    return {"lines": catalog.LINES}


@router.get("/template-variables")
async def get_template_variables() -> dict:
    return {
        "variables": [{"key": k, "label": v} for k, v in workflow.TEMPLATE_VARIABLES.items()],
        "default_subject": workflow.DEFAULT_SUBJECT,
        "default_body": workflow.DEFAULT_BODY,
    }


def _template_context(
    request: InsuranceRequest | None, customer: Customer | None, insurer: Insurer | None
) -> dict[str, str]:
    if request is None:  # preview without a real request: show what each variable means
        context = {key: f"«{label}»" for key, label in workflow.TEMPLATE_VARIABLES.items()}
    else:
        context = {
            "request_number": request.request_number,
            "customer_name": (customer.company_name or customer.full_name) if customer else "",
            "customer_type": CUSTOMER_TYPE_LABELS.get(customer.customer_type, "") if customer else "",
            "insurance_type": catalog.line_label(request.insurance_type),
            "product_type": catalog.product_label(request.insurance_type, request.product_type),
            "coverage_amount": request.coverage_amount or "غير محدد",
            "risk_details": catalog.format_details(request.insurance_type, _details(request))
            or "لا توجد تفاصيل إضافية",
            "notes": request.notes or request.summary or "لا يوجد",
            "insurer_name": "",
            "insurer_contact": "",
        }
    context["today"] = datetime.now(UTC).strftime("%Y-%m-%d")
    if insurer is not None:
        context["insurer_name"] = insurer.name
        context["insurer_contact"] = insurer.contact_name or insurer.name
    return context


async def _find_template(session: AsyncSession, insurer_id: str, category: str) -> InsurerTemplate | None:
    """The insurer's own active template for the category, else the category's default."""
    result = await session.execute(
        select(InsurerTemplate)
        .where(InsurerTemplate.category == category, InsurerTemplate.active.is_(True))
        .where(or_(InsurerTemplate.insurer_id == insurer_id, InsurerTemplate.insurer_id.is_(None)))
        .order_by(InsurerTemplate.insurer_id.is_(None), InsurerTemplate.updated_at.desc())
    )
    return result.scalars().first()


@router.get("/templates", response_model=list[InsurerTemplateResponse])
async def list_templates(
    category: str | None = None,
    insurer_id: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[InsurerTemplateResponse]:
    stmt = select(InsurerTemplate).order_by(InsurerTemplate.category, InsurerTemplate.insurer_id.is_not(None))
    if category:
        stmt = stmt.where(InsurerTemplate.category == category)
    if insurer_id:
        stmt = stmt.where(InsurerTemplate.insurer_id == insurer_id)
    result = await session.execute(stmt)
    return [_template_response(t) for t in result.scalars().all()]


async def _create_template(
    session: AsyncSession, staff: SalesEmployee, insurer_id: str | None, body: InsurerTemplateCreateRequest
) -> InsurerTemplateResponse:
    unknown = workflow.unknown_placeholders(body.subject_template + body.body_template)
    if unknown:
        raise _error(422, "UNKNOWN_TEMPLATE_VARIABLES", f"Unknown variables: {', '.join(unknown)}")
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
    await session.flush()
    audit(
        session,
        staff,
        "template_created",
        "template",
        template.template_id,
        category=body.category,
        insurer_id=insurer_id,
    )
    await session.commit()
    await session.refresh(template)
    return _template_response(template)


@router.post("/templates", response_model=InsurerTemplateResponse, status_code=201)
async def create_template(
    body: DefaultTemplateCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> InsurerTemplateResponse:
    if body.insurer_id:
        await _get_insurer_or_404(session, body.insurer_id)
    return await _create_template(session, staff, body.insurer_id, body)


@router.post("/insurers/{insurer_id}/templates", response_model=InsurerTemplateResponse, status_code=201)
async def create_insurer_template(
    insurer_id: str,
    body: InsurerTemplateCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> InsurerTemplateResponse:
    await _get_insurer_or_404(session, insurer_id)
    return await _create_template(session, staff, insurer_id, body)


@router.get("/insurers/{insurer_id}/templates", response_model=list[InsurerTemplateResponse])
async def list_insurer_templates(
    insurer_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[InsurerTemplateResponse]:
    await _get_insurer_or_404(session, insurer_id)
    return await list_templates(insurer_id=insurer_id, session=session)


@router.patch("/templates/{template_id}", response_model=InsurerTemplateResponse)
async def update_template(
    template_id: str,
    body: InsurerTemplateUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> InsurerTemplateResponse:
    template = await _get_template_or_404(session, template_id)
    changes = body.model_dump(exclude_unset=True)
    unknown = workflow.unknown_placeholders(
        changes.get("subject_template", template.subject_template)
        + changes.get("body_template", template.body_template)
    )
    if unknown:
        raise _error(422, "UNKNOWN_TEMPLATE_VARIABLES", f"Unknown variables: {', '.join(unknown)}")
    if "required_attachments" in changes:
        changes["required_attachments"] = json.dumps(changes["required_attachments"] or [])
    for key, value in changes.items():
        setattr(template, key, value)
    audit(session, staff, "template_updated", "template", template_id, fields=sorted(changes))
    await session.commit()
    await session.refresh(template)
    return _template_response(template)


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> None:
    template = await _get_template_or_404(session, template_id)
    audit(
        session,
        staff,
        "template_deleted",
        "template",
        template_id,
        category=template.category,
        insurer_id=template.insurer_id,
    )
    await session.delete(template)
    await session.commit()


@router.post("/templates/preview", response_model=TemplatePreviewResponse)
async def preview_template(
    body: TemplatePreviewRequest,
    session: AsyncSession = Depends(get_db_session),
) -> TemplatePreviewResponse:
    request = await _get_request_or_404(session, body.request_id) if body.request_id else None
    customer = await session.get(Customer, request.customer_id) if request else None
    insurer = await _get_insurer_or_404(session, body.insurer_id) if body.insurer_id else None
    context = _template_context(request, customer, insurer)
    return TemplatePreviewResponse(
        subject=workflow.render_template(body.subject_template, context),
        body=workflow.render_template(body.body_template, context),
        unknown_placeholders=workflow.unknown_placeholders(body.subject_template + body.body_template),
    )


# --- Employees & customers ---------------------------------------------------


@router.post("/sales-agents", response_model=SalesEmployeeResponse, status_code=201)
@router.post("/employees", response_model=SalesEmployeeResponse, status_code=201)
async def create_employee(
    body: SalesEmployeeCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*MANAGERS)),
) -> SalesEmployeeResponse:
    existing = await session.execute(select(SalesEmployee).where(SalesEmployee.email == str(body.email)))
    if existing.scalar_one_or_none() is not None:
        raise _error(409, "EMPLOYEE_EXISTS", "An employee with this email already exists")

    if body.password is not None and (problem := password_problem(body.password)):
        raise _error(422, "WEAK_PASSWORD", problem)
    employee = SalesEmployee(
        full_name=body.full_name,
        role=body.role,
        email=str(body.email),
        phone=body.phone,
        branch=body.branch,
        manager_id=body.manager_id,
        password_hash=hash_password(body.password) if body.password else None,
    )
    session.add(employee)
    await session.flush()
    audit(
        session, staff, "employee_created", "employee", employee.employee_id, role=body.role, email=employee.email
    )
    await session.commit()
    await session.refresh(employee)
    return SalesEmployeeResponse.model_validate(employee)


@router.patch("/employees/{employee_id}", response_model=SalesEmployeeResponse)
async def update_employee(
    employee_id: str,
    body: SalesEmployeeUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*MANAGERS)),
) -> SalesEmployeeResponse:
    employee = await _get_employee_or_404(session, employee_id)
    changes = body.model_dump(exclude_unset=True)
    if employee.employee_id == staff.employee_id and (
        changes.get("is_active") is False or changes.get("role", Role.MANAGER.value) != Role.MANAGER.value
    ):
        raise _error(409, "CANNOT_DEMOTE_SELF", "A manager cannot deactivate or demote their own account")
    for key, value in changes.items():
        setattr(employee, key, value)
    if changes.get("is_active") is False:
        await session.execute(delete(StaffSession).where(StaffSession.employee_id == employee_id))
    audit(session, staff, "employee_updated", "employee", employee_id, changes=changes)
    await session.commit()
    await session.refresh(employee)
    return SalesEmployeeResponse.model_validate(employee)


@router.post("/employees/{employee_id}/password", status_code=204)
async def set_employee_password(
    employee_id: str,
    body: PasswordSetRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*MANAGERS)),
) -> None:
    """Set or reset an employee's password and sign them out everywhere."""
    employee = await _get_employee_or_404(session, employee_id)
    if problem := password_problem(body.password):
        raise _error(422, "WEAK_PASSWORD", problem)
    employee.password_hash = hash_password(body.password)
    await session.execute(delete(StaffSession).where(StaffSession.employee_id == employee_id))
    audit(session, staff, "password_reset", "employee", employee_id)
    await session.commit()


@router.get("/audit", response_model=list[AuditEventResponse])
async def list_audit_events(
    entity_id: str | None = None,
    actor_id: str | None = None,
    limit: int = 200,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*MANAGERS)),
) -> list[AuditEventResponse]:
    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(min(max(limit, 1), 1000))
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    if actor_id:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    result = await session.execute(stmt)
    return [AuditEventResponse.model_validate(e) for e in result.scalars().all()]


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
    staff: SalesEmployee = Depends(get_current_staff),
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
    await session.flush()
    audit(session, staff, "customer_created", "customer", customer.customer_id)
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


# --- Requests ----------------------------------------------------------------


def _new_request_number() -> str:
    return f"IR-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"


@public_router.post("/portal/requests", response_model=PortalRequestResponse, status_code=201)
async def create_portal_request(
    body: PortalRequestCreate,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> PortalRequestResponse:
    """Public portal: find-or-create the customer by mobile, then open a request."""
    ip = client_ip(http_request)
    if portal_limiter.blocked(ip):
        raise _error(429, "TOO_MANY_REQUESTS", "Too many requests from this address; try again later")
    portal_limiter.hit(ip)
    errors = catalog.validate_details(body.insurance_type, body.product_type, body.details)
    if errors:
        raise _error(422, "INVALID_REQUEST_DETAILS", "؛ ".join(errors))
    if body.customer_type == "business" and not (body.company_name or "").strip():
        raise _error(422, "INVALID_REQUEST_DETAILS", "اسم المنشأة مطلوب لطلبات المنشآت")

    result = await session.execute(select(Customer).where(Customer.phone == body.mobile))
    customer = result.scalars().first()
    if customer is None:
        customer = Customer(
            customer_type=body.customer_type,
            full_name=body.full_name,
            company_name=body.company_name,
            email=str(body.email) if body.email else None,
            phone=body.mobile,
        )
        session.add(customer)
        await session.flush()
    elif body.email and not customer.email:
        customer.email = str(body.email)

    request = InsuranceRequest(
        customer_id=customer.customer_id,
        request_number=_new_request_number(),
        insurance_type=body.insurance_type,
        product_type=body.product_type,
        request_source="portal",
        status=InsuranceRequestStatus.NEW,
        coverage_amount=body.coverage_amount,
        customer_priority=body.customer_priority,
        notes=body.notes,
        request_details=json.dumps(body.details, ensure_ascii=False),
        preferred_insurers="[]",
        consent_at=datetime.now(UTC),
    )
    upload_token, request.upload_token_hash = new_token()
    request.upload_token_expires_at = datetime.now(UTC) + PORTAL_UPLOAD_WINDOW
    session.add(request)
    await session.flush()
    audit(
        session,
        None,
        "portal_request_created",
        "request",
        request.request_id,
        ip=ip,
        customer_id=customer.customer_id,
    )
    await session.commit()
    return PortalRequestResponse(
        request_id=request.request_id,
        request_number=request.request_number,
        status=request.status,
        upload_token=upload_token,
    )


@public_router.post(
    "/portal/requests/{request_id}/documents", response_model=PortalDocumentResponse, status_code=201
)
async def upload_portal_document(
    request_id: str,
    file: UploadFile = File(...),
    upload_token: str = Header(..., alias="X-Upload-Token"),
    session: AsyncSession = Depends(get_db_session),
) -> PortalDocumentResponse:
    """Customer attaches a document to the request they just created (token from the portal response)."""
    request = await session.get(InsuranceRequest, request_id)
    expires_at = request.upload_token_expires_at if request else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        request is None
        or not request.upload_token_hash
        or not hmac.compare_digest(request.upload_token_hash, hash_token(upload_token))
        or expires_at is None
        or expires_at <= datetime.now(UTC)
    ):
        raise _error(403, "UPLOAD_NOT_ALLOWED", "Upload link is invalid or expired")
    count = await session.scalar(select(func.count()).where(RequestDocument.request_id == request_id))
    if (count or 0) >= PORTAL_MAX_DOCUMENTS:
        raise _error(409, "TOO_MANY_DOCUMENTS", f"At most {PORTAL_MAX_DOCUMENTS} documents per request")
    document = await _store_document(
        session,
        request_id,
        file,
        "customer_upload",
        None,
        max_bytes=settings.portal_max_upload_mb * 1024 * 1024,
        allowed=PORTAL_FILE_SIGNATURES,
    )
    audit(session, None, "portal_document_uploaded", "request", request_id, file_name=document.file_name)
    await session.commit()
    return PortalDocumentResponse(document_id=document.document_id, file_name=document.file_name)


@router.post("/requests", response_model=InsuranceRequestResponse, status_code=201)
async def create_request(
    body: InsuranceRequestCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> InsuranceRequestResponse:
    await _get_customer_or_404(session, body.customer_id)

    sales_agent_id = body.sales_agent_id or body.sales_employee_id
    if sales_agent_id:
        await _get_employee_or_404(session, sales_agent_id)

    preferred_list = _normalize_list(body.preferred_insurers)
    if body.preferred_insurer:
        preferred_list.insert(0, body.preferred_insurer)

    request = InsuranceRequest(
        customer_id=body.customer_id,
        request_number=_new_request_number(),
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
    await session.flush()
    audit(session, staff, "request_created", "request", request.request_id)
    await session.commit()
    await session.refresh(request)
    return await _request_response(session, request)


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
    return [await _request_response(session, item) for item in result.scalars().all()]


@router.get("/requests/{request_id}", response_model=InsuranceRequestResponse)
async def get_request(
    request_id: str, session: AsyncSession = Depends(get_db_session)
) -> InsuranceRequestResponse:
    request = await _get_request_or_404(session, request_id)
    return await _request_response(session, request)


@router.patch("/requests/{request_id}/status", response_model=InsuranceRequestResponse)
async def update_request_status(
    request_id: str,
    body: InsuranceRequestStatusUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> InsuranceRequestResponse:
    request = await _get_request_or_404(session, request_id)
    if body.status in (InsuranceRequestStatus.QUOTE_SELECTED, InsuranceRequestStatus.POLICY_ISSUED):
        raise _error(
            409,
            "INVALID_STATUS_TRANSITION",
            "Use the select-quote and issue-policy actions to reach this status",
        )
    _transition(request, body.status)
    if body.notes:
        request.notes = body.notes
    audit(session, staff, "request_status_changed", "request", request_id, status=body.status.value)
    await session.commit()
    await session.refresh(request)
    return await _request_response(session, request)


@router.post("/requests/{request_id}/documents", response_model=RequestDocumentResponse, status_code=201)
async def upload_document(
    request_id: str,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    uploaded_by_employee_id: str | None = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> RequestDocumentResponse:
    await _get_request_or_404(session, request_id)
    if uploaded_by_employee_id:
        await _get_employee_or_404(session, uploaded_by_employee_id)
    document = await _store_document(
        session,
        request_id,
        file,
        document_type,
        uploaded_by_employee_id or staff.employee_id,
        max_bytes=STAFF_MAX_UPLOAD_BYTES,
    )
    audit(session, staff, "document_uploaded", "request", request_id, file_name=document.file_name)
    await session.commit()
    await session.refresh(document)
    return RequestDocumentResponse.model_validate(document)


@router.get("/documents/{document_id}/download")
async def download_document(
    document_id: str,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> FileResponse:
    """Customer documents are never served statically; staff fetch them here (and it's audited)."""
    document = await _get_or_404(session, RequestDocument, document_id, "DOCUMENT_NOT_FOUND", "document")
    path = _resolve_document_path(document.storage_key)
    if path is None or not path.is_file():
        raise _error(404, "DOCUMENT_FILE_MISSING", "The stored file is missing")
    audit(session, staff, "document_downloaded", "request", document.request_id, document_id=document_id)
    await session.commit()
    return FileResponse(path, media_type=document.mime_type, filename=document.file_name)


@router.post(
    "/requests/{request_id}/preferences", response_model=InsuranceRequestPreferenceResponse, status_code=201
)
async def create_request_preferences(
    request_id: str,
    body: InsuranceRequestPreferenceCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> InsuranceRequestPreferenceResponse:
    request = await _get_request_or_404(session, request_id)
    await _get_employee_or_404(session, body.sales_employee_id)

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
    staff: SalesEmployee = Depends(get_current_staff),
):
    request = await _get_request_or_404(session, request_id)
    underwriter_id = body.underwriter_id or body.assigned_underwriter_id
    if not underwriter_id:
        raise _error(400, "UNDERWRITER_ID_REQUIRED", "An underwriter_id is required")
    underwriter = await _get_employee_or_404(session, underwriter_id)

    _transition(request, InsuranceRequestStatus.UNDER_REVIEW)
    request.underwriter_id = underwriter.employee_id
    request.underwriter_status = body.underwriting_status or "under_review"

    assignment = UnderwritingAssignment(
        request_id=request.request_id,
        assigned_by_employee_id=body.sales_employee_id or request.sales_employee_id,
        assigned_to_employee_id=underwriter.employee_id,
        status="assigned",
        notes=body.notes,
    )
    session.add(assignment)
    audit(session, staff, "request_assigned", "request", request_id, underwriter_id=underwriter.employee_id)
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
        raise _error(
            404, "UNDERWRITING_CASE_NOT_FOUND", f"No underwriting assignment found for request '{request_id}'"
        )

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


# --- Insurers ----------------------------------------------------------------


@router.post("/insurers", response_model=InsurerResponse, status_code=201)
async def create_insurer(
    body: InsurerCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
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
    await session.flush()
    audit(session, staff, "insurer_created", "insurer", insurer.insurer_id, email=insurer.email)
    await session.commit()
    await session.refresh(insurer)
    return InsurerResponse.model_validate(insurer)


@router.get("/insurers", response_model=list[InsurerResponse])
async def list_insurers(
    session: AsyncSession = Depends(get_db_session),
) -> list[InsurerResponse]:
    result = await session.execute(select(Insurer).order_by(Insurer.name))
    return [InsurerResponse.model_validate(item) for item in result.scalars().all()]


@router.get("/insurers/{insurer_id}", response_model=InsurerResponse)
async def get_insurer(insurer_id: str, session: AsyncSession = Depends(get_db_session)) -> InsurerResponse:
    return InsurerResponse.model_validate(await _get_insurer_or_404(session, insurer_id))


@router.patch("/insurers/{insurer_id}", response_model=InsurerResponse)
async def update_insurer(
    insurer_id: str,
    body: InsurerUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> InsurerResponse:
    insurer = await _get_insurer_or_404(session, insurer_id)
    changes = body.model_dump(exclude_unset=True)
    if "email" in changes:
        changes["email"] = str(changes["email"])
        clash = await session.execute(
            select(Insurer).where(Insurer.email == changes["email"], Insurer.insurer_id != insurer_id)
        )
        if clash.scalar_one_or_none() is not None:
            raise _error(409, "INSURER_EXISTS", "An insurer with this email already exists")
    for key, value in changes.items():
        setattr(insurer, key, value)
    audit(session, staff, "insurer_updated", "insurer", insurer_id, fields=sorted(changes))
    await session.commit()
    await session.refresh(insurer)
    return InsurerResponse.model_validate(insurer)


# --- Submissions (emails to insurers) ----------------------------------------


async def _draft_submission(
    session: AsyncSession,
    actor: str,
    request: InsuranceRequest,
    insurer: Insurer,
    category: str,
    notes: str | None,
) -> InsuranceSubmission:
    customer = await _get_customer_or_404(session, request.customer_id)
    template = await _find_template(session, insurer.insurer_id, category)
    subject_template = template.subject_template if template else workflow.DEFAULT_SUBJECT
    body_template = template.body_template if template else workflow.DEFAULT_BODY
    context = _template_context(request, customer, insurer)

    submission = InsuranceSubmission(
        request_id=request.request_id,
        insurer_id=insurer.insurer_id,
        category=category,
        subject=workflow.render_template(subject_template, context)[:255],
        email_body=workflow.render_template(body_template, context),
        communication_channel=insurer.communication_channel or "email",
        status=InsuranceSubmissionStatus.PENDING_APPROVAL,
        notes=notes,
    )
    session.add(submission)
    await session.flush()
    _email_audit(
        session,
        submission,
        "created",
        actor,
        category=category,
        request_id=request.request_id,
        insurer_id=insurer.insurer_id,
        template_id=template.template_id if template else None,
    )
    return submission


@router.post("/submissions", response_model=InsuranceSubmissionResponse, status_code=201)
async def create_submission(
    body: InsuranceSubmissionCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> InsuranceSubmissionResponse:
    request = await _get_request_or_404(session, body.request_id)
    insurer = await _get_insurer_or_404(session, body.insurer_id)
    submission = await _draft_submission(session, staff.full_name, request, insurer, body.category, body.notes)
    await session.commit()
    await session.refresh(submission)
    return _submission_response(submission)


@router.post(
    "/requests/{request_id}/submissions", response_model=list[InsuranceSubmissionResponse], status_code=201
)
async def create_submissions_for_request(
    request_id: str,
    body: BulkSubmissionCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> list[InsuranceSubmissionResponse]:
    """Draft one email per selected insurer, using the request's line as the template category.

    Moves the request to ready_for_quote. Drafts still need approval before sending.
    """
    request = await _get_request_or_404(session, request_id)
    if request.status not in workflow.OPEN_STATUSES:
        raise _error(409, "REQUEST_NOT_OPEN", "Submissions can only be drafted for open requests")
    insurers = [await _get_insurer_or_404(session, insurer_id) for insurer_id in dict.fromkeys(body.insurer_ids)]
    inactive = [i.name for i in insurers if not i.is_active]
    if inactive:
        raise _error(409, "INSURER_INACTIVE", f"Inactive insurers: {', '.join(inactive)}")

    _transition(request, InsuranceRequestStatus.READY_FOR_QUOTE)
    submissions = [
        await _draft_submission(session, staff.full_name, request, i, request.insurance_type, body.notes)
        for i in insurers
    ]
    audit(session, staff, "emails_drafted", "request", request_id, insurers=[i.insurer_id for i in insurers])
    await session.commit()
    for submission in submissions:
        await session.refresh(submission)
    return [_submission_response(s) for s in submissions]


@router.get("/submissions", response_model=list[InsuranceSubmissionResponse])
async def list_submissions(
    request_id: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[InsuranceSubmissionResponse]:
    stmt = select(InsuranceSubmission).order_by(
        InsuranceSubmission.created_at.desc(), InsuranceSubmission.submission_id
    )
    if request_id:
        stmt = stmt.where(InsuranceSubmission.request_id == request_id)
    result = await session.execute(stmt)
    return [_submission_response(item) for item in result.scalars().all()]


@router.get("/submissions/{submission_id}", response_model=InsuranceSubmissionResponse)
async def get_submission(
    submission_id: str, session: AsyncSession = Depends(get_db_session)
) -> InsuranceSubmissionResponse:
    return _submission_response(await _get_submission_or_404(session, submission_id))


@router.patch("/submissions/{submission_id}", response_model=InsuranceSubmissionResponse)
async def update_submission(
    submission_id: str,
    body: InsuranceSubmissionUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> InsuranceSubmissionResponse:
    """Edit the email before it goes out. Editing an approved email sends it back for approval."""
    submission = await _get_submission_or_404(session, submission_id)
    editable = {
        InsuranceSubmissionStatus.PENDING_APPROVAL,
        InsuranceSubmissionStatus.APPROVED,
        InsuranceSubmissionStatus.FAILED,
    }
    if submission.status not in editable:
        raise _error(409, "SUBMISSION_LOCKED", "Only unsent emails can be edited")
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(submission, key, value)
    if {"subject", "email_body"} & changes.keys():
        submission.status = InsuranceSubmissionStatus.PENDING_APPROVAL
        submission.approved_for_send = False
    _email_audit(session, submission, "edited", staff.full_name, fields=sorted(changes))
    await session.commit()
    await session.refresh(submission)
    return _submission_response(submission)


@router.post("/submissions/{submission_id}/approve", response_model=InsuranceSubmissionResponse)
async def approve_submission(
    submission_id: str,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> InsuranceSubmissionResponse:
    submission = await _get_submission_or_404(session, submission_id)
    if submission.status == InsuranceSubmissionStatus.SENT:
        raise _error(409, "SUBMISSION_LOCKED", "This email was already sent")
    submission.approved_for_send = True
    submission.status = InsuranceSubmissionStatus.APPROVED
    _email_audit(session, submission, "approved", staff.full_name, approved_for_send=True)
    await session.commit()
    await session.refresh(submission)
    return _submission_response(submission)


def _build_insurer_email_message(
    submission: InsuranceSubmission,
    insurer: Insurer,
    attachment_documents: Sequence[RequestDocument],
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.smtp_from_email
    message["To"] = insurer.email
    message["Subject"] = submission.subject
    message.set_content(submission.email_body)

    for document in attachment_documents:
        file_path = _resolve_document_path(document.storage_key)
        if file_path is None or not file_path.is_file():
            continue
        if "/" in (document.mime_type or ""):
            maintype, subtype = document.mime_type.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(
            file_path.read_bytes(), maintype=maintype, subtype=subtype, filename=document.file_name
        )
    return message


@router.post("/submissions/{submission_id}/send", response_model=InsuranceSubmissionResponse)
async def send_submission(
    submission_id: str,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> InsuranceSubmissionResponse:
    """Email the approved submission to the insurer.

    The status only becomes `sent` after the SMTP server accepted the message. With email
    delivery disabled this refuses (409) instead of pretending; use mark-sent when the email
    went out another way. An SMTP failure records `failed` + the error so it can be retried.
    """
    submission = await _get_submission_or_404(session, submission_id)
    if not submission.approved_for_send:
        raise _error(400, "SUBMISSION_NOT_APPROVED", "This submission must be approved before sending")
    if submission.status == InsuranceSubmissionStatus.SENT:
        raise _error(409, "SUBMISSION_LOCKED", "This email was already sent")
    if not settings.email_delivery_enabled:
        raise _error(
            409,
            "EMAIL_DELIVERY_DISABLED",
            "Email delivery is not configured (EMAIL_DELIVERY_ENABLED=false). "
            "Configure SMTP, or mark the submission as sent manually.",
        )

    insurer = await _get_insurer_or_404(session, submission.insurer_id)
    documents = await session.execute(
        select(RequestDocument).where(RequestDocument.request_id == submission.request_id)
    )
    message = _build_insurer_email_message(submission, insurer, documents.scalars().all())

    try:
        with smtplib.SMTP(settings.smtp_host, int(settings.smtp_port), timeout=30) as smtp_client:
            if settings.smtp_use_tls:
                smtp_client.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp_client.login(settings.smtp_username, settings.smtp_password)
            smtp_client.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        submission.status = InsuranceSubmissionStatus.FAILED
        submission.delivery_error = f"{type(exc).__name__}: {exc}"[:2000]
        _email_audit(session, submission, "send_failed", staff.full_name, error=submission.delivery_error)
    else:
        submission.status = InsuranceSubmissionStatus.SENT
        submission.sent_at = datetime.now(UTC)
        submission.delivery_error = None
        _email_audit(session, submission, "sent", staff.full_name, sent_at=submission.sent_at, to=insurer.email)
    await session.commit()
    await session.refresh(submission)
    return _submission_response(submission)


@router.post("/submissions/{submission_id}/mark-sent", response_model=InsuranceSubmissionResponse)
async def mark_submission_sent(
    submission_id: str,
    body: SubmissionMarkSentRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> InsuranceSubmissionResponse:
    """Record that an approved email was sent outside BUD (e.g. from the employee's mailbox)."""
    submission = await _get_submission_or_404(session, submission_id)
    if not submission.approved_for_send:
        raise _error(400, "SUBMISSION_NOT_APPROVED", "This submission must be approved before sending")
    if submission.status == InsuranceSubmissionStatus.SENT:
        raise _error(409, "SUBMISSION_LOCKED", "This email was already sent")
    submission.status = InsuranceSubmissionStatus.SENT
    submission.sent_at = datetime.now(UTC)
    submission.delivery_error = None
    _email_audit(session, submission, "marked_sent", staff.full_name, sent_at=submission.sent_at, note=body.note)
    await session.commit()
    await session.refresh(submission)
    return _submission_response(submission)


# --- Quotes ------------------------------------------------------------------


@router.post("/submissions/{submission_id}/quotes", response_model=InsuranceQuoteResponse, status_code=201)
async def create_quote(
    submission_id: str,
    body: InsuranceQuoteCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
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
    await session.flush()
    audit(session, staff, "quote_recorded", "request", submission.request_id, premium=quote.premium)
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


async def _quotes_for_request(session: AsyncSession, request_id: str) -> Sequence[InsuranceQuote]:
    result = await session.execute(
        select(InsuranceQuote)
        .join(InsuranceSubmission, InsuranceSubmission.submission_id == InsuranceQuote.submission_id)
        .where(InsuranceSubmission.request_id == request_id)
        .order_by(InsuranceQuote.created_at.desc())
    )
    return result.scalars().all()


@router.get("/requests/{request_id}/quotes", response_model=list[InsuranceQuoteResponse])
async def list_quotes_for_request(
    request_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[InsuranceQuoteResponse]:
    await _get_request_or_404(session, request_id)
    return [
        InsuranceQuoteResponse.model_validate(quote) for quote in await _quotes_for_request(session, request_id)
    ]


@router.get("/requests/{request_id}/best-quote", response_model=InsuranceQuoteResponse)
async def get_best_quote_for_request(
    request_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> InsuranceQuoteResponse:
    await _get_request_or_404(session, request_id)
    quotes = await _quotes_for_request(session, request_id)
    if not quotes:
        raise _error(404, "QUOTES_NOT_FOUND", f"No quotes found for request '{request_id}'")

    def rank(quote: InsuranceQuote) -> tuple[float, float]:
        premium = _parse_money(quote.premium)
        coverage = _parse_money(quote.coverage_limit)
        return (premium if premium is not None else float("inf"), -(coverage if coverage is not None else 0.0))

    return InsuranceQuoteResponse.model_validate(min(quotes, key=rank))


@router.post("/requests/{request_id}/quotes/{quote_id}/select", response_model=InsuranceQuoteResponse)
async def select_quote_for_request(
    request_id: str,
    quote_id: str,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> InsuranceQuoteResponse:
    request = await _get_request_or_404(session, request_id)
    quote = await session.get(InsuranceQuote, quote_id)
    if quote is None:
        raise _error(404, "QUOTE_NOT_FOUND", f"No quote found with id '{quote_id}'")

    submission = await session.get(InsuranceSubmission, quote.submission_id)
    if submission is None or submission.request_id != request_id:
        raise _error(400, "QUOTE_NOT_FOR_REQUEST", "This quote does not belong to the selected request")
    if request.status not in workflow.OPEN_STATUSES:
        raise _error(409, "REQUEST_NOT_OPEN", "A quote can only be selected for an open request")

    # A received quote means the request was out for pricing, whatever status it was left in.
    if request.status != InsuranceRequestStatus.QUOTE_SELECTED:
        request.status = InsuranceRequestStatus.READY_FOR_QUOTE
    _transition(request, InsuranceRequestStatus.QUOTE_SELECTED)
    request.selected_quote_id = quote.quote_id
    audit(session, staff, "quote_selected", "request", request_id, quote_id=quote_id)
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
        raise _error(
            404, "SELECTED_QUOTE_NOT_FOUND", f"Selected quote '{request.selected_quote_id}' was not found"
        )
    return InsuranceQuoteResponse.model_validate(quote)


# --- Policies ----------------------------------------------------------------


@router.post("/requests/{request_id}/issue-policy", response_model=InsurancePolicyResponse, status_code=201)
async def issue_policy(
    request_id: str,
    body: InsurancePolicyCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> InsurancePolicyResponse:
    request = await _get_request_or_404(session, request_id)
    if not request.selected_quote_id:
        raise _error(400, "NO_SELECTED_QUOTE", f"No quote has been selected for request '{request_id}'")

    quote = await session.get(InsuranceQuote, request.selected_quote_id)
    if quote is None:
        raise _error(
            404, "SELECTED_QUOTE_NOT_FOUND", f"Selected quote '{request.selected_quote_id}' was not found"
        )

    submission = await session.get(InsuranceSubmission, quote.submission_id)
    if submission is None or submission.request_id != request_id:
        raise _error(400, "QUOTE_NOT_FOR_REQUEST", "The selected quote does not belong to this request")

    existing = await session.execute(select(InsurancePolicy).where(InsurancePolicy.request_id == request_id))
    existing_policy = existing.scalar_one_or_none()
    if existing_policy is not None:
        return InsurancePolicyResponse.model_validate(existing_policy)

    _transition(request, InsuranceRequestStatus.POLICY_ISSUED)
    effective_from = body.effective_from or datetime.now(UTC)
    policy = InsurancePolicy(
        request_id=request.request_id,
        insurer_id=submission.insurer_id,
        selected_quote_id=quote.quote_id,
        policy_number=f"POL-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}",
        status=InsurancePolicyStatus.ISSUED,
        coverage_amount=request.coverage_amount or quote.coverage_limit,
        premium_amount=quote.premium,
        currency=quote.currency or "SAR",
        effective_from=effective_from,
        effective_to=body.effective_to or effective_from,
        issued_at=datetime.now(UTC),
        notes=body.notes,
    )
    session.add(policy)
    await session.flush()
    audit(session, staff, "policy_issued", "request", request_id, policy_number=policy.policy_number)
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
    policy = await _get_or_404(session, InsurancePolicy, policy_id, "POLICY_NOT_FOUND", "policy")
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
    await _get_request_or_404(session, request_id)
    result = await session.execute(select(InsurancePolicy).where(InsurancePolicy.request_id == request_id))
    policy = result.scalar_one_or_none()
    if policy is None:
        raise _error(404, "POLICY_NOT_FOUND", f"No policy found for request '{request_id}'")
    return InsurancePolicyResponse.model_validate(policy)


@router.post("/policies/{policy_id}/activate", response_model=InsurancePolicyResponse)
async def activate_policy(
    policy_id: str,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> InsurancePolicyResponse:
    policy = await _get_or_404(session, InsurancePolicy, policy_id, "POLICY_NOT_FOUND", "policy")
    policy.status = InsurancePolicyStatus.ACTIVE
    policy.issued_at = policy.issued_at or datetime.now(UTC)
    audit(session, staff, "policy_activated", "policy", policy_id)
    await session.commit()
    await session.refresh(policy)
    return InsurancePolicyResponse.model_validate(policy)


@router.patch("/policies/{policy_id}/status", response_model=InsurancePolicyResponse)
async def update_policy_status(
    policy_id: str,
    body: InsurancePolicyStatusUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> InsurancePolicyResponse:
    policy = await _get_or_404(session, InsurancePolicy, policy_id, "POLICY_NOT_FOUND", "policy")
    policy.status = body.status
    if body.notes:
        policy.notes = body.notes
    audit(session, staff, "policy_status_changed", "policy", policy_id, status=body.status.value)
    await session.commit()
    await session.refresh(policy)
    return InsurancePolicyResponse.model_validate(policy)
