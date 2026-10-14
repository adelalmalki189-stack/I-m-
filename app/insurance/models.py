from __future__ import annotations

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid


def _values(enum_cls: type[PyEnum]) -> list[str]:
    """Persist enum *values* ("new"), matching the migrations, not member names ("NEW")."""
    return [member.value for member in enum_cls]


class CustomerType(str, PyEnum):
    INDIVIDUAL = "individual"
    BUSINESS = "business"


class InsuranceRequestStatus(str, PyEnum):
    NEW = "new"
    NEEDS_INFO = "needs_info"
    UNDER_REVIEW = "under_review"
    READY_FOR_QUOTE = "ready_for_quote"
    QUOTE_SELECTED = "quote_selected"
    POLICY_ISSUED = "policy_issued"
    CLOSED = "closed"


class InsurancePolicyStatus(str, PyEnum):
    DRAFT = "draft"
    ISSUED = "issued"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class InsuranceEmployeeRole(str, PyEnum):
    SALES_AGENT = "sales_agent"
    UNDERWRITER = "underwriter"
    MANAGER = "manager"


class UnderwritingStatus(str, PyEnum):
    PENDING = "pending"
    UNDER_REVIEW = "under_review"
    NEEDS_INFO = "needs_info"
    READY_FOR_EMAIL = "ready_for_email"
    REJECTED = "rejected"
    CLOSED = "closed"


class InsurerCommunicationChannel(str, PyEnum):
    EMAIL = "email"
    MANUAL = "manual"
    MIXED = "mixed"


class InsuranceSubmissionStatus(str, PyEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SENT = "sent"
    FAILED = "failed"
    FOLLOW_UP_REQUIRED = "follow_up_required"
    REJECTED = "rejected"
    CLOSED = "closed"


class Customer(Base, TimestampMixin):
    __tablename__ = "insurance_customers"

    customer_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    customer_type: Mapped[str] = mapped_column(String(20), nullable=False, default=CustomerType.INDIVIDUAL.value)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    requests: Mapped[list["InsuranceRequest"]] = relationship(back_populates="customer")


class SalesEmployee(Base, TimestampMixin):
    __tablename__ = "insurance_sales_employees"

    employee_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(80), nullable=False, default="sales_agent")
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(120), nullable=True)
    manager_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    requests_handled: Mapped[list["InsuranceRequest"]] = relationship(
        foreign_keys="InsuranceRequest.sales_employee_id",
        back_populates="sales_employee",
    )
    assignments_received: Mapped[list["InsuranceRequest"]] = relationship(
        foreign_keys="InsuranceRequest.underwriter_id",
        back_populates="underwriter",
    )
    created_assignments: Mapped[list["UnderwritingAssignment"]] = relationship(
        foreign_keys="UnderwritingAssignment.assigned_by_employee_id",
        back_populates="assigned_by_employee",
    )
    assigned_to_requests: Mapped[list["UnderwritingAssignment"]] = relationship(
        foreign_keys="UnderwritingAssignment.assigned_to_employee_id",
        back_populates="assigned_to_employee",
    )
    request_preferences: Mapped[list["InsuranceRequestPreference"]] = relationship(
        foreign_keys="InsuranceRequestPreference.sales_employee_id",
        back_populates="sales_employee",
    )
    underwriting_assignments: Mapped[list["InsuranceUnderwritingCase"]] = relationship(
        foreign_keys="InsuranceUnderwritingCase.sales_employee_id",
        back_populates="sales_employee",
    )
    underwriting_cases: Mapped[list["InsuranceUnderwritingCase"]] = relationship(
        foreign_keys="InsuranceUnderwritingCase.assigned_underwriter_id",
        back_populates="assigned_underwriter",
    )


class RequestDocument(Base, TimestampMixin):
    __tablename__ = "insurance_request_documents"

    document_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(String(36), ForeignKey("insurance_requests.request_id"), nullable=False)
    document_type: Mapped[str] = mapped_column(String(80), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    uploaded_by_employee_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("insurance_sales_employees.employee_id"),
        nullable=True,
    )

    request: Mapped[InsuranceRequest] = relationship(back_populates="documents")
    uploaded_by_employee: Mapped[SalesEmployee | None] = relationship(foreign_keys=[uploaded_by_employee_id])


class InsuranceRequest(Base, TimestampMixin):
    __tablename__ = "insurance_requests"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("insurance_customers.customer_id"), nullable=False)
    request_number: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    insurance_type: Mapped[str] = mapped_column(String(80), nullable=False)
    product_type: Mapped[str] = mapped_column(String(80), nullable=False)
    request_source: Mapped[str] = mapped_column(String(50), nullable=False, default="portal")
    status: Mapped[InsuranceRequestStatus] = mapped_column(
        Enum(InsuranceRequestStatus, native_enum=False, values_callable=_values, name="insurance_request_status"),
        nullable=False,
        default=InsuranceRequestStatus.NEW,
    )
    sales_employee_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("insurance_sales_employees.employee_id"),
        nullable=True,
    )
    underwriter_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("insurance_sales_employees.employee_id"),
        nullable=True,
    )
    underwriter_status: Mapped[str] = mapped_column(String(30), nullable=False, default="new")
    preferred_insurers: Mapped[str | None] = mapped_column(Text, nullable=True, default="[]")
    preferred_premium_range: Mapped[str | None] = mapped_column(String(100), nullable=True)
    premium_budget: Mapped[str | None] = mapped_column(String(50), nullable=True)
    coverage_limit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    customer_priority: Mapped[str | None] = mapped_column(String(30), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    coverage_amount: Mapped[str | None] = mapped_column(String(50), nullable=True)
    preferred_insurer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_preferences: Mapped[str | None] = mapped_column(Text, nullable=True)
    sales_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_quote_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_details: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: catalog field key -> value
    last_status_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="requests")
    documents: Mapped[list["RequestDocument"]] = relationship(back_populates="request")
    submissions: Mapped[list["InsuranceSubmission"]] = relationship(back_populates="request")
    preferences: Mapped[list["InsuranceRequestPreference"]] = relationship(back_populates="request")
    underwriting_case: Mapped["InsuranceUnderwritingCase"] = relationship(back_populates="request", uselist=False)
    sales_employee: Mapped[SalesEmployee | None] = relationship(
        foreign_keys=[sales_employee_id],
        back_populates="requests_handled",
    )
    underwriter: Mapped[SalesEmployee | None] = relationship(
        foreign_keys=[underwriter_id],
        back_populates="assignments_received",
    )
    underwriting_assignments: Mapped[list["UnderwritingAssignment"]] = relationship(back_populates="request")
    policies: Mapped[list["InsurancePolicy"]] = relationship(back_populates="request")


class InsurancePolicy(Base, TimestampMixin):
    __tablename__ = "insurance_policies"

    policy_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(String(36), ForeignKey("insurance_requests.request_id"), nullable=False, unique=True)
    insurer_id: Mapped[str] = mapped_column(String(36), ForeignKey("insurance_insurers.insurer_id"), nullable=False)
    selected_quote_id: Mapped[str] = mapped_column(String(36), nullable=False)
    policy_number: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    status: Mapped[InsurancePolicyStatus] = mapped_column(
        Enum(InsurancePolicyStatus, native_enum=False, values_callable=_values, name="insurance_policy_status"),
        nullable=False,
        default=InsurancePolicyStatus.ISSUED,
    )
    coverage_amount: Mapped[str | None] = mapped_column(String(50), nullable=True)
    premium_amount: Mapped[str | None] = mapped_column(String(50), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="SAR")
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    request: Mapped[InsuranceRequest] = relationship(back_populates="policies")
    insurer: Mapped[Insurer] = relationship(back_populates="policies")


class UnderwritingAssignment(Base, TimestampMixin):
    __tablename__ = "insurance_underwriting_assignments"

    assignment_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(String(36), ForeignKey("insurance_requests.request_id"), nullable=False)
    assigned_by_employee_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("insurance_sales_employees.employee_id"),
        nullable=True,
    )
    assigned_to_employee_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("insurance_sales_employees.employee_id"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="assigned")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    request: Mapped[InsuranceRequest] = relationship(back_populates="underwriting_assignments")
    assigned_by_employee: Mapped[SalesEmployee | None] = relationship(
        foreign_keys=[assigned_by_employee_id],
        back_populates="created_assignments",
    )
    assigned_to_employee: Mapped[SalesEmployee | None] = relationship(
        foreign_keys=[assigned_to_employee_id],
        back_populates="assigned_to_requests",
    )


class InsuranceRequestPreference(Base, TimestampMixin):
    __tablename__ = "insurance_request_preferences"

    preference_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(String(36), ForeignKey("insurance_requests.request_id"), nullable=False)
    sales_employee_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("insurance_sales_employees.employee_id"),
        nullable=False,
    )
    preferred_insurers: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_premium_range: Mapped[str | None] = mapped_column(String(100), nullable=True)
    preferred_coverage_limit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    customer_priority: Mapped[str | None] = mapped_column(String(30), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    request: Mapped[InsuranceRequest] = relationship(back_populates="preferences")
    sales_employee: Mapped[SalesEmployee] = relationship(back_populates="request_preferences")


class InsuranceUnderwritingCase(Base, TimestampMixin):
    __tablename__ = "insurance_underwriting_cases"

    case_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(String(36), ForeignKey("insurance_requests.request_id"), nullable=False, unique=True)
    sales_employee_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("insurance_sales_employees.employee_id"),
        nullable=True,
    )
    assigned_underwriter_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("insurance_sales_employees.employee_id"),
        nullable=True,
    )
    underwriting_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=UnderwritingStatus.PENDING.value
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    request: Mapped[InsuranceRequest] = relationship(back_populates="underwriting_case")
    sales_employee: Mapped[SalesEmployee | None] = relationship(
        foreign_keys=[sales_employee_id],
        back_populates="underwriting_assignments",
    )
    assigned_underwriter: Mapped[SalesEmployee | None] = relationship(
        foreign_keys=[assigned_underwriter_id],
        back_populates="underwriting_cases",
    )


class Insurer(Base, TimestampMixin):
    __tablename__ = "insurance_insurers"

    insurer_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    contact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    communication_channel: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=InsurerCommunicationChannel.EMAIL.value,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    templates: Mapped[list["InsurerTemplate"]] = relationship(back_populates="insurer")
    submissions: Mapped[list["InsuranceSubmission"]] = relationship(back_populates="insurer")
    policies: Mapped[list["InsurancePolicy"]] = relationship(back_populates="insurer")


class InsurerTemplate(Base, TimestampMixin):
    __tablename__ = "insurance_insurer_templates"

    template_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # NULL insurer_id = the default template for this category (insurance line), used for
    # any insurer that has no template of its own.
    insurer_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("insurance_insurers.insurer_id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_template: Mapped[str] = mapped_column(String(255), nullable=False)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    required_attachments: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    insurer: Mapped[Insurer | None] = relationship(back_populates="templates")


class InsuranceSubmission(Base, TimestampMixin):
    __tablename__ = "insurance_submissions"

    submission_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(String(36), ForeignKey("insurance_requests.request_id"), nullable=False)
    insurer_id: Mapped[str] = mapped_column(String(36), ForeignKey("insurance_insurers.insurer_id"), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email_body: Mapped[str] = mapped_column(Text, nullable=False)
    communication_channel: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=InsurerCommunicationChannel.EMAIL.value,
    )
    status: Mapped[InsuranceSubmissionStatus] = mapped_column(
        Enum(InsuranceSubmissionStatus, native_enum=False, values_callable=_values, name="insurance_submission_status"),
        nullable=False,
        default=InsuranceSubmissionStatus.PENDING_APPROVAL,
    )
    approved_for_send: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    request: Mapped[InsuranceRequest] = relationship(back_populates="submissions")
    insurer: Mapped[Insurer] = relationship(back_populates="submissions")
    quotes: Mapped[list["InsuranceQuote"]] = relationship(back_populates="submission")
    audit_logs: Mapped[list["EmailAuditLog"]] = relationship(back_populates="submission")


class InsuranceQuote(Base, TimestampMixin):
    __tablename__ = "insurance_quotes"

    quote_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    submission_id: Mapped[str] = mapped_column(String(36), ForeignKey("insurance_submissions.submission_id"), nullable=False)
    premium: Mapped[str | None] = mapped_column(String(50), nullable=True)
    coverage_limit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    exclusions: Mapped[str | None] = mapped_column(Text, nullable=True)
    conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    validity_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    source_channel: Mapped[str] = mapped_column(String(30), nullable=False, default=InsurerCommunicationChannel.EMAIL.value)

    submission: Mapped[InsuranceSubmission] = relationship(back_populates="quotes")


class EmailAuditLog(Base, TimestampMixin):
    __tablename__ = "insurance_email_audit_logs"

    audit_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    submission_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("insurance_submissions.submission_id"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    submission: Mapped[InsuranceSubmission | None] = relationship(back_populates="audit_logs")
