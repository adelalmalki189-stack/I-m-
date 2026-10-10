from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.insurance.models import InsurancePolicyStatus, InsuranceRequestStatus, InsuranceSubmissionStatus


class CustomerCreateRequest(BaseModel):
    customer_type: str = Field(default="individual")
    full_name: str = Field(..., min_length=2, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    notes: str | None = None


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: str
    customer_type: str
    full_name: str
    company_name: str | None = None
    email: str | None = None
    phone: str | None = None
    notes: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SalesEmployeeCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=200)
    role: str = Field(default="sales_agent", max_length=80)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=32)
    branch: str | None = Field(default=None, max_length=120)
    manager_id: str | None = None


class SalesEmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employee_id: str
    sales_agent_id: str | None = None
    full_name: str
    role: str
    email: str
    phone: str | None = None
    branch: str | None = None
    manager_id: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def set_sales_agent_id(cls, data):
        if isinstance(data, dict):
            data.setdefault("sales_agent_id", data.get("employee_id"))
        return data


class InsuranceRequestCreateRequest(BaseModel):
    customer_id: str
    sales_agent_id: str | None = None
    sales_employee_id: str | None = None
    insurance_type: str = Field(..., min_length=2, max_length=80)
    product_type: str = Field(..., min_length=2, max_length=80)
    request_source: str = Field(default="portal")
    coverage_amount: str | None = Field(default=None, max_length=50)
    summary: str | None = None
    notes: str | None = None
    sales_notes: str | None = None
    preferred_insurer: str | None = Field(default=None, max_length=200)
    preferred_insurers: list[str] = Field(default_factory=list)
    preferred_premium_range: str | None = Field(default=None, max_length=100)
    premium_budget: str | None = Field(default=None, max_length=50)
    coverage_limit: str | None = Field(default=None, max_length=50)
    customer_priority: str | None = Field(default=None, max_length=30)
    customer_preferences: str | None = None


class InsuranceRequestStatusUpdateRequest(BaseModel):
    status: InsuranceRequestStatus
    notes: str | None = None


class RequestDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: str
    request_id: str
    document_type: str
    file_name: str
    mime_type: str
    size_bytes: int
    storage_key: str
    is_verified: bool
    uploaded_by_employee_id: str | None = None
    created_at: datetime


class InsuranceRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: str
    customer_id: str
    request_number: str
    insurance_type: str
    product_type: str
    request_source: str
    status: InsuranceRequestStatus
    sales_agent_id: str | None = None
    sales_employee_id: str | None = None
    underwriter_id: str | None = None
    underwriter_status: str | None = None
    preferred_insurer: str | None = None
    preferred_insurers: list[str] = Field(default_factory=list)
    preferred_premium_range: str | None = None
    premium_budget: str | None = None
    coverage_limit: str | None = None
    customer_priority: str | None = None
    customer_preferences: str | None = None
    sales_notes: str | None = None
    selected_quote_id: str | None = None
    coverage_amount: str | None = None
    summary: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime
    documents: list[RequestDocumentResponse] = []

    @model_validator(mode="before")
    @classmethod
    def set_sales_agent_id(cls, data):
        if isinstance(data, dict):
            if data.get("sales_agent_id") is None:
                data["sales_agent_id"] = data.get("sales_employee_id")
        return data


class UnderwritingAssignmentRequest(BaseModel):
    sales_employee_id: str | None = None
    underwriter_id: str | None = None
    assigned_underwriter_id: str | None = None
    underwriting_status: str = Field(default="under_review", max_length=30)
    notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def resolve_underwriter_id(cls, data):
        if isinstance(data, dict):
            if data.get("underwriter_id") is None:
                data["underwriter_id"] = data.get("assigned_underwriter_id")
        return data


class UnderwritingAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    assignment_id: str
    request_id: str
    assigned_by_employee_id: str | None = None
    assigned_to_employee_id: str | None = None
    assigned_underwriter_id: str | None = None
    status: str
    underwriting_status: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def set_underwriter_alias(cls, data):
        if isinstance(data, dict):
            data.setdefault("assigned_underwriter_id", data.get("assigned_to_employee_id"))
            data.setdefault("underwriting_status", data.get("status"))
        return data


class EmployeeCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=200)
    role: str = Field(default="sales_agent", max_length=30)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=32)
    team: str | None = Field(default=None, max_length=100)
    manager_id: str | None = Field(default=None, max_length=36)


class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employee_id: str
    full_name: str
    role: str
    email: str
    phone: str | None = None
    team: str | None = None
    manager_id: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class InsuranceRequestPreferenceCreateRequest(BaseModel):
    sales_employee_id: str
    preferred_insurers: list[str] = Field(default_factory=list)
    preferred_premium_range: str | None = Field(default=None, max_length=100)
    preferred_coverage_limit: str | None = Field(default=None, max_length=50)
    customer_priority: str | None = Field(default=None, max_length=30)
    notes: str | None = None


class InsuranceRequestPreferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    preference_id: str
    request_id: str
    sales_employee_id: str
    preferred_insurers: list[str]
    preferred_premium_range: str | None = None
    preferred_coverage_limit: str | None = None
    customer_priority: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def normalize_prefs(cls, data):
        if isinstance(data, dict):
            insurers = data.get("preferred_insurers")
            if isinstance(insurers, str):
                import json
                try:
                    data["preferred_insurers"] = json.loads(insurers)
                except Exception:
                    data["preferred_insurers"] = [insurers]
        return data


class InsuranceUnderwritingAssignmentRequest(BaseModel):
    sales_employee_id: str
    assigned_underwriter_id: str
    underwriting_status: str = Field(default="under_review", max_length=30)
    notes: str | None = None


class InsuranceUnderwritingCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: str
    request_id: str
    sales_employee_id: str | None = None
    assigned_underwriter_id: str | None = None
    underwriting_status: str
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class InsurerCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    email: EmailStr
    contact_name: str | None = Field(default=None, max_length=200)
    communication_channel: str = Field(default="email", max_length=30)
    notes: str | None = None


class InsurerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    insurer_id: str
    name: str
    email: str
    contact_name: str | None = None
    communication_channel: str
    is_active: bool
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class InsurerTemplateCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    category: str = Field(..., min_length=2, max_length=80)
    subject_template: str = Field(..., min_length=3)
    body_template: str = Field(..., min_length=3)
    required_attachments: list[str] = Field(default_factory=list)
    active: bool = True


class InsurerTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    template_id: str
    insurer_id: str
    name: str
    category: str
    subject_template: str
    body_template: str
    required_attachments: list[str]
    active: bool
    created_at: datetime
    updated_at: datetime


class InsuranceSubmissionCreateRequest(BaseModel):
    request_id: str
    insurer_id: str
    category: str = Field(..., min_length=2, max_length=80)
    notes: str | None = None


class InsuranceSubmissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    submission_id: str
    request_id: str
    insurer_id: str
    category: str
    subject: str
    email_body: str
    communication_channel: str
    status: InsuranceSubmissionStatus
    approved_for_send: bool
    sent_at: datetime | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class InsuranceQuoteCreateRequest(BaseModel):
    premium: str | float
    coverage_limit: str | float | None = None
    exclusions: str | None = None
    conditions: str | None = None
    validity_date: datetime | None = None
    currency: str = Field(default="SAR", max_length=10)
    source_channel: str = Field(default="email", max_length=30)


class InsurancePolicyCreateRequest(BaseModel):
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    notes: str | None = None


class InsurancePolicyStatusUpdateRequest(BaseModel):
    status: InsurancePolicyStatus
    notes: str | None = None


class InsurancePolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    policy_id: str
    request_id: str
    insurer_id: str
    selected_quote_id: str
    policy_number: str
    status: InsurancePolicyStatus
    coverage_amount: str | None = None
    premium_amount: str | None = None
    currency: str
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    issued_at: datetime | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class InsuranceQuoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    quote_id: str
    submission_id: str
    premium: str
    coverage_limit: str | None = None
    exclusions: str | None = None
    conditions: str | None = None
    validity_date: datetime | None = None
    currency: str
    source_channel: str
    created_at: datetime
    updated_at: datetime
