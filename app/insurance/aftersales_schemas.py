from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.insurance.models import ClaimStatus, InstallmentStatus, InsurancePolicyStatus
from app.insurance.schemas import normalize_saudi_mobile

Money = Decimal


class InstallmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    installment_id: str
    policy_id: str
    sequence: int
    amount: Money
    due_date: date
    status: InstallmentStatus
    is_overdue: bool = False
    paid_at: datetime | None = None
    payment_method: str | None = None
    payment_reference: str | None = None


class InstallmentRow(InstallmentResponse):
    policy_number: str
    customer_id: str
    customer_name: str
    insurer_name: str


class PaymentRecordRequest(BaseModel):
    payment_method: str = Field(..., pattern="^(bank_transfer|card|cash|sadad|other)$")
    payment_reference: str | None = Field(default=None, max_length=100)
    paid_at: datetime | None = None


class PolicyView(BaseModel):
    policy_id: str
    policy_number: str
    request_id: str
    request_number: str
    customer_id: str
    customer_name: str
    insurer_id: str
    insurer_name: str
    insurance_type: str
    insurance_type_label: str
    product_type_label: str
    status: InsurancePolicyStatus
    effective_status: str  # status, or "expired" once the term has ended
    premium_amount: Money | None
    coverage_amount: str | None
    currency: str
    effective_from: datetime | None
    effective_to: datetime | None
    days_to_expiry: int | None
    issued_at: datetime | None
    commission_rate: Money | None
    commission_amount: Money | None
    commission_received_at: datetime | None
    commission_reference: str | None
    installments_total: int
    installments_paid: int
    outstanding_amount: Money
    overdue_amount: Money
    renewal_request_id: str | None
    open_claims: int
    notes: str | None


class PolicyDetail(PolicyView):
    installments: list[InstallmentResponse]


class PolicyCancelRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


class CommissionReceivedRequest(BaseModel):
    reference: str | None = Field(default=None, max_length=100)
    received_at: datetime | None = None


class RenewalResponse(BaseModel):
    request_id: str
    request_number: str


class CommissionRate(BaseModel):
    line: str = Field(..., max_length=80)
    rate: Money = Field(..., ge=0, le=100)


class CommissionRatesPayload(BaseModel):
    default_rate: Money | None = Field(default=None, ge=0, le=100)
    rates: list[CommissionRate] = Field(default_factory=list)


class ClaimCreateRequest(BaseModel):
    policy_id: str
    incident_date: date
    description: str = Field(..., min_length=5, max_length=5000)
    claimed_amount: Money | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)


class ClaimStatusUpdateRequest(BaseModel):
    status: ClaimStatus
    approved_amount: Money | None = Field(default=None, ge=0)
    paid_amount: Money | None = Field(default=None, ge=0)
    insurer_reference: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class ClaimView(BaseModel):
    claim_id: str
    claim_number: str
    policy_id: str
    policy_number: str
    customer_id: str
    customer_name: str
    insurer_name: str
    insurance_type_label: str
    status: ClaimStatus
    allowed_transitions: list[str]
    incident_date: date
    description: str
    claimed_amount: Money | None
    approved_amount: Money | None
    paid_amount: Money | None
    insurer_reference: str | None
    notes: str | None
    last_status_change_at: datetime | None
    created_at: datetime


class CustomerUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = None
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        return normalize_saudi_mobile(value) if value else value


class CustomerSummary(BaseModel):
    customer_id: str
    customer_type: str
    display_name: str
    full_name: str
    company_name: str | None
    email: str | None
    phone: str | None
    requests_count: int
    open_requests: int
    active_policies: int
    total_premium: Money
    open_claims: int
    next_expiry: datetime | None
    created_at: datetime


class CustomerRequestBrief(BaseModel):
    request_id: str
    request_number: str
    insurance_type_label: str
    product_type_label: str
    status: str
    created_at: datetime


class CustomerProfile(BaseModel):
    customer: CustomerSummary
    notes: str | None
    requests: list[CustomerRequestBrief]
    policies: list[PolicyView]
    claims: list[ClaimView]


class CommissionRow(BaseModel):
    policy_id: str
    policy_number: str
    insurer_id: str
    insurer_name: str
    customer_name: str
    insurance_type_label: str
    premium_amount: Money | None
    commission_rate: Money | None
    commission_amount: Money | None
    issued_at: datetime | None
    commission_received_at: datetime | None
    commission_reference: str | None
