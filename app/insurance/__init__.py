"""Insurance brokerage domain, isolated from the trading domain.

This module is intentionally separated from the existing market-data and
options-trading code so new insurance workflows remain independent.
"""

from app.insurance.models import (
    Customer,
    EmailAuditLog,
    Insurer,
    InsuranceQuote,
    InsuranceRequest,
    InsuranceSubmission,
    InsurerTemplate,
    RequestDocument,
    SalesEmployee,
    UnderwritingAssignment,
)

__all__ = [
    "Customer",
    "InsuranceRequest",
    "RequestDocument",
    "SalesEmployee",
    "UnderwritingAssignment",
    "Insurer",
    "InsurerTemplate",
    "InsuranceSubmission",
    "InsuranceQuote",
    "EmailAuditLog",
]
