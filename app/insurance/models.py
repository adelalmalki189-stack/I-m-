from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum as PyEnum
from enum import StrEnum

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid


def _values(enum_cls: type[PyEnum]) -> list[str]:
    """Persist enum *values* ("new"), matching the migrations, not member names ("NEW")."""
    return [member.value for member in enum_cls]


class CustomerType(StrEnum):
    INDIVIDUAL = "individual"
    BUSINESS = "business"


class InsuranceRequestStatus(StrEnum):
    NEW = "new"
    NEEDS_INFO = "needs_info"
    UNDER_REVIEW = "under_review"
    READY_FOR_QUOTE = "ready_for_quote"
    QUOTE_SELECTED = "quote_selected"
    POLICY_ISSUED = "policy_issued"
    CLOSED = "closed"


class InsurancePolicyStatus(StrEnum):
    DRAFT = "draft"
    ISSUED = "issued"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ClaimStatus(StrEnum):
    REPORTED = "reported"
    SUBMITTED = "submitted"
    UNDER_ASSESSMENT = "under_assessment"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"
    CLOSED = "closed"


class InstallmentStatus(StrEnum):
    DUE = "due"
    PAID = "paid"
    CANCELLED = "cancelled"


class InsuranceEmployeeRole(StrEnum):
    SALES_AGENT = "sales_agent"
    UNDERWRITER = "underwriter"
    CLAIMS_OFFICER = "claims_officer"
    CUSTOMER_SERVICE = "customer_service"
    MANAGER = "manager"


class UnderwritingStatus(StrEnum):
    PENDING = "pending"
    UNDER_REVIEW = "under_review"
    NEEDS_INFO = "needs_info"
    READY_FOR_EMAIL = "ready_for_email"
    REJECTED = "rejected"
    CLOSED = "closed"


class InsurerCommunicationChannel(StrEnum):
    EMAIL = "email"
    MANUAL = "manual"
    MIXED = "mixed"


class InsuranceSubmissionStatus(StrEnum):
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
    # The customer asked not to receive WhatsApp/SMS messages from BUD.
    messaging_opt_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    requests: Mapped[list[InsuranceRequest]] = relationship(back_populates="customer")


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
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)  # NULL = cannot log in
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requests_handled: Mapped[list[InsuranceRequest]] = relationship(
        foreign_keys="InsuranceRequest.sales_employee_id",
        back_populates="sales_employee",
    )
    assignments_received: Mapped[list[InsuranceRequest]] = relationship(
        foreign_keys="InsuranceRequest.underwriter_id",
        back_populates="underwriter",
    )
    created_assignments: Mapped[list[UnderwritingAssignment]] = relationship(
        foreign_keys="UnderwritingAssignment.assigned_by_employee_id",
        back_populates="assigned_by_employee",
    )
    assigned_to_requests: Mapped[list[UnderwritingAssignment]] = relationship(
        foreign_keys="UnderwritingAssignment.assigned_to_employee_id",
        back_populates="assigned_to_employee",
    )
    request_preferences: Mapped[list[InsuranceRequestPreference]] = relationship(
        foreign_keys="InsuranceRequestPreference.sales_employee_id",
        back_populates="sales_employee",
    )
    underwriting_assignments: Mapped[list[InsuranceUnderwritingCase]] = relationship(
        foreign_keys="InsuranceUnderwritingCase.sales_employee_id",
        back_populates="sales_employee",
    )
    underwriting_cases: Mapped[list[InsuranceUnderwritingCase]] = relationship(
        foreign_keys="InsuranceUnderwritingCase.assigned_underwriter_id",
        back_populates="assigned_underwriter",
    )


class RequestDocument(Base, TimestampMixin):
    __tablename__ = "insurance_request_documents"

    document_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_requests.request_id"), nullable=False
    )
    # Set when the file belongs to a claim (accident photos, reports); still filed under the policy's request.
    claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
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
    # Automatic review on upload (app/insurance/review.py), then a staff decision.
    # review_status: passed | warning | failed (automatic), accepted | rejected (staff).
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="passed")
    review_findings: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of Arabic sentences
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    request: Mapped[InsuranceRequest] = relationship(back_populates="documents")
    uploaded_by_employee: Mapped[SalesEmployee | None] = relationship(foreign_keys=[uploaded_by_employee_id])


class InsuranceRequest(Base, TimestampMixin):
    __tablename__ = "insurance_requests"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_customers.customer_id"), nullable=False
    )
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
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renewal_of_policy_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Short-lived token returned to the portal so the customer can attach documents to
    # their own request without a staff session (sha256 of the token).
    upload_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    upload_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="requests")
    documents: Mapped[list[RequestDocument]] = relationship(back_populates="request")
    submissions: Mapped[list[InsuranceSubmission]] = relationship(back_populates="request")
    preferences: Mapped[list[InsuranceRequestPreference]] = relationship(back_populates="request")
    underwriting_case: Mapped[InsuranceUnderwritingCase] = relationship(back_populates="request", uselist=False)
    sales_employee: Mapped[SalesEmployee | None] = relationship(
        foreign_keys=[sales_employee_id],
        back_populates="requests_handled",
    )
    underwriter: Mapped[SalesEmployee | None] = relationship(
        foreign_keys=[underwriter_id],
        back_populates="assignments_received",
    )
    underwriting_assignments: Mapped[list[UnderwritingAssignment]] = relationship(back_populates="request")
    policies: Mapped[list[InsurancePolicy]] = relationship(back_populates="request")


class InsurancePolicy(Base, TimestampMixin):
    __tablename__ = "insurance_policies"

    policy_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_requests.request_id"), nullable=False, unique=True
    )
    insurer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_insurers.insurer_id"), nullable=False
    )
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
    # Broker commission owed by the insurer for this policy.
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)  # percent
    commission_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    commission_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    commission_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)

    request: Mapped[InsuranceRequest] = relationship(back_populates="policies")
    insurer: Mapped[Insurer] = relationship(back_populates="policies")


class UnderwritingAssignment(Base, TimestampMixin):
    __tablename__ = "insurance_underwriting_assignments"

    assignment_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_requests.request_id"), nullable=False
    )
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
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_requests.request_id"), nullable=False
    )
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
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_requests.request_id"), nullable=False, unique=True
    )
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

    templates: Mapped[list[InsurerTemplate]] = relationship(back_populates="insurer")
    submissions: Mapped[list[InsuranceSubmission]] = relationship(back_populates="insurer")
    policies: Mapped[list[InsurancePolicy]] = relationship(back_populates="insurer")


class InsurerTemplate(Base, TimestampMixin):
    __tablename__ = "insurance_insurer_templates"

    template_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # NULL insurer_id = the default template for this category (insurance line), used for
    # any insurer that has no template of its own.
    insurer_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("insurance_insurers.insurer_id"), nullable=True
    )
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
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_requests.request_id"), nullable=False
    )
    insurer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_insurers.insurer_id"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email_body: Mapped[str] = mapped_column(Text, nullable=False)
    communication_channel: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=InsurerCommunicationChannel.EMAIL.value,
    )
    status: Mapped[InsuranceSubmissionStatus] = mapped_column(
        Enum(
            InsuranceSubmissionStatus,
            native_enum=False,
            values_callable=_values,
            name="insurance_submission_status",
        ),
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
    quotes: Mapped[list[InsuranceQuote]] = relationship(back_populates="submission")
    audit_logs: Mapped[list[EmailAuditLog]] = relationship(back_populates="submission")


class InsuranceQuote(Base, TimestampMixin):
    __tablename__ = "insurance_quotes"

    quote_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    submission_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_submissions.submission_id"), nullable=False
    )
    premium: Mapped[str | None] = mapped_column(String(50), nullable=True)
    coverage_limit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    exclusions: Mapped[str | None] = mapped_column(Text, nullable=True)
    conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    validity_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Broker commission percent offered by the insurer in this quote.
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # Quotes read from an insurer's email by AI wait for an underwriter to confirm them.
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    inbound_email_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    extraction_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_channel: Mapped[str] = mapped_column(
        String(30), nullable=False, default=InsurerCommunicationChannel.EMAIL.value
    )

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


class StaffSession(Base, TimestampMixin):
    """A logged-in staff browser session. Only the sha256 of the cookie token is stored."""

    __tablename__ = "insurance_staff_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    employee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_sales_employees.employee_id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)


class AuditEvent(Base, TimestampMixin):
    """Who did what to which record — one row per sensitive action."""

    __tablename__ = "insurance_audit_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    actor_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("insurance_sales_employees.employee_id"), nullable=True
    )
    actor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PremiumInstallment(Base, TimestampMixin):
    """One premium payment the customer owes on a policy."""

    __tablename__ = "insurance_premium_installments"

    installment_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    policy_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_policies.policy_id"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[InstallmentStatus] = mapped_column(
        Enum(InstallmentStatus, native_enum=False, values_callable=_values, name="installment_status"),
        nullable=False,
        default=InstallmentStatus.DUE,
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)


class InsuranceClaim(Base, TimestampMixin):
    __tablename__ = "insurance_claims"

    claim_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    claim_number: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    policy_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_policies.policy_id"), nullable=False, index=True
    )
    status: Mapped[ClaimStatus] = mapped_column(
        Enum(ClaimStatus, native_enum=False, values_callable=_values, name="claim_status"),
        nullable=False,
        default=ClaimStatus.REPORTED,
    )
    incident_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    claimed_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    approved_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    insurer_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_status_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthorizationLetterStatus(StrEnum):
    PENDING_SIGNATURE = "pending_signature"
    SIGNED = "signed"
    REVOKED = "revoked"


class AuthorizationLetter(Base, TimestampMixin):
    """The customer authorizes BUD to request quotes from insurers on their behalf.

    BUD issues the text; the customer signs it after proving their mobile with a one-time
    code. The signed text is frozen (body_text + content_hash) and anyone holding the
    verification code can check it on the public verification page."""

    __tablename__ = "insurance_authorization_letters"

    letter_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    letter_number: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_requests.request_id"), nullable=False, index=True
    )
    status: Mapped[AuthorizationLetterStatus] = mapped_column(
        Enum(
            AuthorizationLetterStatus,
            native_enum=False,
            values_callable=_values,
            name="authorization_letter_status",
        ),
        nullable=False,
        default=AuthorizationLetterStatus.PENDING_SIGNATURE,
    )
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    issued_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    signer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signer_id_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)  # never the full ID
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signed_user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signed_mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)  # verified by the one-time code
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # sha256 of the signed record
    verification_code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # signed copy in the request files


class ListAnalysis(Base, TimestampMixin):
    """An underwriter's review of an Excel list (census, fleet, assets) attached to a request.

    Holds the decisions (which column is which, how raw classes map to target classes,
    which classes and insurers are targeted) and the latest result; finalizing writes the
    formatted, insurer-ready workbook into the request's files."""

    __tablename__ = "insurance_list_analyses"

    analysis_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_requests.request_id"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)  # the uploaded list
    profile: Mapped[str] = mapped_column(String(20), nullable=False)
    sheet_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    options: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON: mapping, classes, insurers
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")  # draft | finalized
    output_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    analyzed_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
