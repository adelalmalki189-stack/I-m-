"""After-sale API: policy book, renewals, premium collection, commissions, claims, customer files, reports."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, overload
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import UNDERWRITING, audit, get_current_staff, require_roles
from app.api.deps import get_db_session
from app.insurance import aftersales, catalog
from app.insurance.aftersales_schemas import (
    ClaimCreateRequest,
    ClaimStatusUpdateRequest,
    ClaimView,
    CommissionReceivedRequest,
    CommissionRow,
    CustomerProfile,
    CustomerRequestBrief,
    CustomerSummary,
    CustomerUpdateRequest,
    InstallmentResponse,
    InstallmentRow,
    PaymentRecordRequest,
    PolicyCancelRequest,
    PolicyDetail,
    PolicyView,
    RenewalResponse,
)
from app.insurance.models import (
    ClaimStatus,
    Customer,
    InstallmentStatus,
    InsuranceClaim,
    InsurancePolicy,
    InsurancePolicyStatus,
    InsuranceRequest,
    InsuranceRequestStatus,
    Insurer,
    PremiumInstallment,
    SalesEmployee,
)
from app.insurance.workflow import OPEN_STATUSES

router = APIRouter(prefix="/insurance", tags=["after-sales"], dependencies=[Depends(get_current_staff)])

ZERO = Decimal("0.00")
LIVE_POLICY = {InsurancePolicyStatus.ISSUED, InsurancePolicyStatus.ACTIVE}
OPEN_CLAIMS = {ClaimStatus.REPORTED, ClaimStatus.SUBMITTED, ClaimStatus.UNDER_ASSESSMENT, ClaimStatus.APPROVED}


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message})


@overload
def _utc(value: datetime) -> datetime: ...
@overload
def _utc(value: None) -> None: ...
@overload
def _utc(value: datetime | None) -> datetime | None: ...
def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:  # SQLite returns naive datetimes
        return value.replace(tzinfo=UTC)
    return value


def _today() -> date:
    return datetime.now(UTC).date()


async def _by_id(session: AsyncSession, model: Any, key: Any, ids: Iterable[str]) -> dict[str, Any]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    result = await session.execute(select(model).where(key.in_(wanted)))
    rows: list[Any] = list(result.scalars().all())
    return {getattr(row, key.key): row for row in rows}


async def _get(session: AsyncSession, model: Any, key: str, code: str) -> Any:
    item = await session.get(model, key)
    if item is None:
        raise _error(404, code, f"Not found: {key}")
    return item


def _display_name(customer: Customer | None) -> str:
    return (customer.company_name or customer.full_name) if customer else "عميل"


# --- Policy book ----------------------------------------------------------------


def _installment_response(item: PremiumInstallment, today: date) -> InstallmentResponse:
    response = InstallmentResponse.model_validate(item)
    response.is_overdue = item.status == InstallmentStatus.DUE and item.due_date < today
    return response


async def _policy_views(session: AsyncSession, policies: Sequence[InsurancePolicy]) -> list[PolicyDetail]:
    policy_ids = [p.policy_id for p in policies]
    requests = await _by_id(
        session, InsuranceRequest, InsuranceRequest.request_id, (p.request_id for p in policies)
    )
    customers = await _by_id(session, Customer, Customer.customer_id, (r.customer_id for r in requests.values()))
    insurers = await _by_id(session, Insurer, Insurer.insurer_id, (p.insurer_id for p in policies))

    installments: dict[str, list[PremiumInstallment]] = defaultdict(list)
    open_claims: Counter[str] = Counter()
    renewals: dict[str, str] = {}
    if policy_ids:
        installment_rows = await session.execute(
            select(PremiumInstallment)
            .where(PremiumInstallment.policy_id.in_(policy_ids))
            .order_by(PremiumInstallment.sequence)
        )
        for item in installment_rows.scalars():
            installments[item.policy_id].append(item)
        claim_rows = await session.execute(
            select(InsuranceClaim.policy_id).where(
                InsuranceClaim.policy_id.in_(policy_ids), InsuranceClaim.status.in_(OPEN_CLAIMS)
            )
        )
        open_claims.update(claim_rows.scalars())
        renewal_rows = await session.execute(
            select(InsuranceRequest.renewal_of_policy_id, InsuranceRequest.request_id).where(
                InsuranceRequest.renewal_of_policy_id.in_(policy_ids),
                InsuranceRequest.status != InsuranceRequestStatus.CLOSED,
            )
        )
        renewals = {policy_id: request_id for policy_id, request_id in renewal_rows.all() if policy_id}

    now, today = datetime.now(UTC), _today()
    views = []
    for policy in policies:
        request = requests.get(policy.request_id)
        line = request.insurance_type if request else ""
        end = _utc(policy.effective_to)
        items = installments[policy.policy_id]
        due = [i for i in items if i.status == InstallmentStatus.DUE]
        effective_status = policy.status.value
        if policy.status in LIVE_POLICY and end is not None and end < now:
            effective_status = "expired"
        views.append(
            PolicyDetail(
                policy_id=policy.policy_id,
                policy_number=policy.policy_number,
                request_id=policy.request_id,
                request_number=request.request_number if request else "",
                customer_id=request.customer_id if request else "",
                customer_name=_display_name(customers.get(request.customer_id) if request else None),
                insurer_id=policy.insurer_id,
                insurer_name=insurers[policy.insurer_id].name if policy.insurer_id in insurers else "",
                insurance_type=line,
                insurance_type_label=catalog.line_label(line),
                product_type_label=catalog.product_label(line, request.product_type) if request else "",
                status=policy.status,
                effective_status=effective_status,
                premium_amount=aftersales.to_money(policy.premium_amount),
                coverage_amount=policy.coverage_amount,
                currency=policy.currency,
                effective_from=_utc(policy.effective_from),
                effective_to=end,
                days_to_expiry=(end.date() - today).days if end else None,
                issued_at=_utc(policy.issued_at),
                commission_rate=policy.commission_rate,
                commission_amount=policy.commission_amount,
                commission_received_at=_utc(policy.commission_received_at),
                commission_reference=policy.commission_reference,
                installments_total=len([i for i in items if i.status != InstallmentStatus.CANCELLED]),
                installments_paid=len([i for i in items if i.status == InstallmentStatus.PAID]),
                outstanding_amount=sum((i.amount for i in due), ZERO),
                overdue_amount=sum((i.amount for i in due if i.due_date < today), ZERO),
                renewal_request_id=renewals.get(policy.policy_id),
                open_claims=open_claims[policy.policy_id],
                notes=policy.notes,
                installments=[_installment_response(i, today) for i in items],
            )
        )
    return views


@router.get("/portfolio/policies", response_model=list[PolicyView])
async def policy_book(
    expiring_within_days: int | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[PolicyDetail]:
    result = await session.execute(select(InsurancePolicy).order_by(InsurancePolicy.effective_to))
    views = await _policy_views(session, result.scalars().all())
    if expiring_within_days is not None:
        views = [
            v
            for v in views
            if v.effective_status in ("issued", "active")
            and v.days_to_expiry is not None
            and 0 <= v.days_to_expiry <= expiring_within_days
        ]
    return views


@router.get("/portfolio/policies/{policy_id}", response_model=PolicyDetail)
async def policy_detail(policy_id: str, session: AsyncSession = Depends(get_db_session)) -> PolicyDetail:
    policy = await _get(session, InsurancePolicy, policy_id, "POLICY_NOT_FOUND")
    return (await _policy_views(session, [policy]))[0]


@router.post("/policies/{policy_id}/renew", response_model=RenewalResponse, status_code=201)
async def renew_policy(
    policy_id: str,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> RenewalResponse:
    """Open a new request for the same customer and risk, linked to this policy."""
    policy = await _get(session, InsurancePolicy, policy_id, "POLICY_NOT_FOUND")
    if policy.status == InsurancePolicyStatus.CANCELLED:
        raise _error(409, "POLICY_CANCELLED", "A cancelled policy cannot be renewed")
    existing = await session.execute(
        select(InsuranceRequest).where(
            InsuranceRequest.renewal_of_policy_id == policy_id,
            InsuranceRequest.status != InsuranceRequestStatus.CLOSED,
        )
    )
    if (open_renewal := existing.scalars().first()) is not None:
        raise _error(409, "RENEWAL_EXISTS", f"Renewal request {open_renewal.request_number} already exists")
    original = await _get(session, InsuranceRequest, policy.request_id, "REQUEST_NOT_FOUND")
    renewal = InsuranceRequest(
        customer_id=original.customer_id,
        request_number=f"IR-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}",
        insurance_type=original.insurance_type,
        product_type=original.product_type,
        request_source="renewal",
        status=InsuranceRequestStatus.NEW,
        coverage_amount=policy.coverage_amount or original.coverage_amount,
        customer_priority=original.customer_priority,
        request_details=original.request_details,
        preferred_insurers="[]",
        sales_employee_id=staff.employee_id,
        renewal_of_policy_id=policy_id,
        notes=f"تجديد للوثيقة {policy.policy_number}",
    )
    session.add(renewal)
    await session.flush()
    audit(session, staff, "renewal_started", "policy", policy_id, request_id=renewal.request_id)
    await session.commit()
    return RenewalResponse(request_id=renewal.request_id, request_number=renewal.request_number)


@router.post("/policies/{policy_id}/cancel", response_model=PolicyDetail)
async def cancel_policy(
    policy_id: str,
    body: PolicyCancelRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> PolicyDetail:
    policy = await _get(session, InsurancePolicy, policy_id, "POLICY_NOT_FOUND")
    if policy.status == InsurancePolicyStatus.CANCELLED:
        raise _error(409, "POLICY_CANCELLED", "Policy is already cancelled")
    policy.status = InsurancePolicyStatus.CANCELLED
    policy.notes = "\n".join(filter(None, [policy.notes, f"إلغاء: {body.reason}"]))
    result = await session.execute(
        select(PremiumInstallment).where(
            PremiumInstallment.policy_id == policy_id, PremiumInstallment.status == InstallmentStatus.DUE
        )
    )
    for item in result.scalars():
        item.status = InstallmentStatus.CANCELLED
    audit(session, staff, "policy_cancelled", "policy", policy_id, reason=body.reason)
    await session.commit()
    return (await _policy_views(session, [policy]))[0]


# --- Premium collection & commissions -------------------------------------------


@router.get("/portfolio/installments", response_model=list[InstallmentRow])
async def list_installments(
    status: str | None = None,  # due | overdue | paid | cancelled
    session: AsyncSession = Depends(get_db_session),
) -> list[InstallmentRow]:
    result = await session.execute(select(PremiumInstallment).order_by(PremiumInstallment.due_date))
    items = result.scalars().all()
    policies = await _by_id(session, InsurancePolicy, InsurancePolicy.policy_id, (i.policy_id for i in items))
    requests = await _by_id(
        session, InsuranceRequest, InsuranceRequest.request_id, (p.request_id for p in policies.values())
    )
    customers = await _by_id(session, Customer, Customer.customer_id, (r.customer_id for r in requests.values()))
    insurers = await _by_id(session, Insurer, Insurer.insurer_id, (p.insurer_id for p in policies.values()))
    today = _today()
    rows = []
    for item in items:
        base = _installment_response(item, today)
        if status == "overdue" and not base.is_overdue:
            continue
        if status in ("due", "paid", "cancelled") and item.status.value != status:
            continue
        policy = policies[item.policy_id]
        request = requests.get(policy.request_id)
        rows.append(
            InstallmentRow(
                **base.model_dump(),
                policy_number=policy.policy_number,
                customer_id=request.customer_id if request else "",
                customer_name=_display_name(customers.get(request.customer_id) if request else None),
                insurer_name=insurers[policy.insurer_id].name if policy.insurer_id in insurers else "",
            )
        )
    return rows


@router.post("/installments/{installment_id}/pay", response_model=InstallmentResponse)
async def record_payment(
    installment_id: str,
    body: PaymentRecordRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> InstallmentResponse:
    item = await _get(session, PremiumInstallment, installment_id, "INSTALLMENT_NOT_FOUND")
    if item.status != InstallmentStatus.DUE:
        raise _error(409, "INSTALLMENT_NOT_DUE", "Only due installments can be marked as paid")
    item.status = InstallmentStatus.PAID
    item.paid_at = body.paid_at or datetime.now(UTC)
    item.payment_method = body.payment_method
    item.payment_reference = body.payment_reference
    audit(
        session,
        staff,
        "premium_collected",
        "policy",
        item.policy_id,
        amount=item.amount,
        sequence=item.sequence,
        method=body.payment_method,
    )
    await session.commit()
    await session.refresh(item)
    return _installment_response(item, _today())


@router.get("/portfolio/commissions", response_model=list[CommissionRow])
async def list_commissions(
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> list[CommissionRow]:
    result = await session.execute(
        select(InsurancePolicy)
        .where(InsurancePolicy.status != InsurancePolicyStatus.CANCELLED)
        .order_by(InsurancePolicy.issued_at.desc())
    )
    views = await _policy_views(session, result.scalars().all())
    return [CommissionRow(**v.model_dump(include=set(CommissionRow.model_fields))) for v in views]


@router.post("/policies/{policy_id}/commission-received", response_model=PolicyDetail)
async def commission_received(
    policy_id: str,
    body: CommissionReceivedRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> PolicyDetail:
    policy = await _get(session, InsurancePolicy, policy_id, "POLICY_NOT_FOUND")
    if policy.commission_amount is None:
        raise _error(409, "NO_COMMISSION", "The selected quote had no commission rate")
    if policy.commission_received_at is not None:
        raise _error(409, "COMMISSION_ALREADY_RECEIVED", "Commission already recorded as received")
    policy.commission_received_at = body.received_at or datetime.now(UTC)
    policy.commission_reference = body.reference
    audit(session, staff, "commission_received", "policy", policy_id, amount=policy.commission_amount)
    await session.commit()
    return (await _policy_views(session, [policy]))[0]


# --- Claims ---------------------------------------------------------------------


async def _claim_views(session: AsyncSession, claims: Sequence[InsuranceClaim]) -> list[ClaimView]:
    policies = await _by_id(session, InsurancePolicy, InsurancePolicy.policy_id, (c.policy_id for c in claims))
    requests = await _by_id(
        session, InsuranceRequest, InsuranceRequest.request_id, (p.request_id for p in policies.values())
    )
    customers = await _by_id(session, Customer, Customer.customer_id, (r.customer_id for r in requests.values()))
    insurers = await _by_id(session, Insurer, Insurer.insurer_id, (p.insurer_id for p in policies.values()))
    views = []
    for claim in claims:
        policy = policies[claim.policy_id]
        request = requests.get(policy.request_id)
        views.append(
            ClaimView(
                claim_id=claim.claim_id,
                claim_number=claim.claim_number,
                policy_id=claim.policy_id,
                policy_number=policy.policy_number,
                customer_id=request.customer_id if request else "",
                customer_name=_display_name(customers.get(request.customer_id) if request else None),
                insurer_name=insurers[policy.insurer_id].name if policy.insurer_id in insurers else "",
                insurance_type_label=catalog.line_label(request.insurance_type) if request else "",
                status=claim.status,
                allowed_transitions=aftersales.claim_next(claim.status),
                incident_date=claim.incident_date,
                description=claim.description,
                claimed_amount=claim.claimed_amount,
                approved_amount=claim.approved_amount,
                paid_amount=claim.paid_amount,
                insurer_reference=claim.insurer_reference,
                notes=claim.notes,
                last_status_change_at=_utc(claim.last_status_change_at),
                created_at=_utc(claim.created_at),
            )
        )
    return views


@router.post("/claims", response_model=ClaimView, status_code=201)
async def create_claim(
    body: ClaimCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> ClaimView:
    policy = await _get(session, InsurancePolicy, body.policy_id, "POLICY_NOT_FOUND")
    if policy.status == InsurancePolicyStatus.CANCELLED:
        raise _error(409, "POLICY_CANCELLED", "Claims cannot be filed on a cancelled policy")
    start, end = _utc(policy.effective_from), _utc(policy.effective_to)
    if (start and body.incident_date < start.date()) or (end and body.incident_date > end.date()):
        raise _error(422, "INCIDENT_OUTSIDE_COVERAGE", "The incident date is outside the policy period")
    if body.incident_date > _today():
        raise _error(422, "INCIDENT_IN_FUTURE", "The incident date cannot be in the future")
    claim = InsuranceClaim(
        claim_number=f"CL-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:6].upper()}",
        policy_id=policy.policy_id,
        incident_date=body.incident_date,
        description=body.description,
        claimed_amount=body.claimed_amount,
        notes=body.notes,
        status=ClaimStatus.REPORTED,
        last_status_change_at=datetime.now(UTC),
    )
    session.add(claim)
    await session.flush()
    audit(session, staff, "claim_reported", "claim", claim.claim_id, policy_id=policy.policy_id)
    await session.commit()
    await session.refresh(claim)
    return (await _claim_views(session, [claim]))[0]


@router.get("/claims", response_model=list[ClaimView])
async def list_claims(
    status: ClaimStatus | None = None,
    policy_id: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[ClaimView]:
    stmt = select(InsuranceClaim).order_by(InsuranceClaim.created_at.desc())
    if status:
        stmt = stmt.where(InsuranceClaim.status == status)
    if policy_id:
        stmt = stmt.where(InsuranceClaim.policy_id == policy_id)
    result = await session.execute(stmt)
    return await _claim_views(session, result.scalars().all())


@router.get("/claims/{claim_id}", response_model=ClaimView)
async def get_claim(claim_id: str, session: AsyncSession = Depends(get_db_session)) -> ClaimView:
    claim = await _get(session, InsuranceClaim, claim_id, "CLAIM_NOT_FOUND")
    return (await _claim_views(session, [claim]))[0]


@router.post("/claims/{claim_id}/status", response_model=ClaimView)
async def update_claim_status(
    claim_id: str,
    body: ClaimStatusUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> ClaimView:
    claim = await _get(session, InsuranceClaim, claim_id, "CLAIM_NOT_FOUND")
    if body.status not in aftersales.CLAIM_TRANSITIONS[claim.status]:
        raise _error(
            409,
            "INVALID_STATUS_TRANSITION",
            f"Cannot move a claim from '{claim.status.value}' to '{body.status.value}'",
        )
    if body.status == ClaimStatus.APPROVED:
        if body.approved_amount is None:
            raise _error(422, "AMOUNT_REQUIRED", "approved_amount is required to approve a claim")
        claim.approved_amount = body.approved_amount
    if body.status == ClaimStatus.PAID:
        paid = body.paid_amount if body.paid_amount is not None else claim.approved_amount
        if paid is None:
            raise _error(422, "AMOUNT_REQUIRED", "paid_amount is required")
        claim.paid_amount = paid
    if body.insurer_reference:
        claim.insurer_reference = body.insurer_reference
    if body.notes:
        claim.notes = "\n".join(filter(None, [claim.notes, body.notes]))
    claim.status = body.status
    claim.last_status_change_at = datetime.now(UTC)
    audit(
        session,
        staff,
        "claim_status_changed",
        "claim",
        claim_id,
        status=body.status.value,
        approved_amount=claim.approved_amount,
        paid_amount=claim.paid_amount,
    )
    await session.commit()
    await session.refresh(claim)
    return (await _claim_views(session, [claim]))[0]


# --- Customer files --------------------------------------------------------------


def _customer_summary(
    customer: Customer, requests: list[InsuranceRequest], policies: list[PolicyDetail], claims: list[ClaimView]
) -> CustomerSummary:
    live = [p for p in policies if p.effective_status in ("issued", "active")]
    upcoming = [p.effective_to for p in live if p.effective_to]
    return CustomerSummary(
        customer_id=customer.customer_id,
        customer_type=customer.customer_type,
        display_name=_display_name(customer),
        full_name=customer.full_name,
        company_name=customer.company_name,
        email=customer.email,
        phone=customer.phone,
        requests_count=len(requests),
        open_requests=len([r for r in requests if r.status in OPEN_STATUSES]),
        active_policies=len(live),
        total_premium=sum((p.premium_amount or ZERO for p in live), ZERO),
        open_claims=len([c for c in claims if c.status in OPEN_CLAIMS]),
        next_expiry=min(upcoming) if upcoming else None,
        created_at=_utc(customer.created_at),
    )


async def _customer_data(session: AsyncSession, customer_ids: list[str]):
    request_rows = await session.execute(
        select(InsuranceRequest)
        .where(InsuranceRequest.customer_id.in_(customer_ids))
        .order_by(InsuranceRequest.created_at.desc())
    )
    requests = request_rows.scalars().all()
    by_request = {r.request_id: r for r in requests}
    policy_rows = await session.execute(
        select(InsurancePolicy).where(InsurancePolicy.request_id.in_(list(by_request)))
    )
    policies = await _policy_views(session, policy_rows.scalars().all())
    claim_rows = await session.execute(
        select(InsuranceClaim).where(InsuranceClaim.policy_id.in_([p.policy_id for p in policies]))
    )
    claims = await _claim_views(session, claim_rows.scalars().all())
    group: dict[str, tuple[list[InsuranceRequest], list[PolicyDetail], list[ClaimView]]] = defaultdict(
        lambda: ([], [], [])
    )
    for r in requests:
        group[r.customer_id][0].append(r)
    for p in policies:
        group[p.customer_id][1].append(p)
    for c in claims:
        group[c.customer_id][2].append(c)
    return group


@router.get("/portfolio/customers", response_model=list[CustomerSummary])
async def customer_list(
    q: str | None = None, session: AsyncSession = Depends(get_db_session)
) -> list[CustomerSummary]:
    result = await session.execute(select(Customer).order_by(Customer.created_at.desc()))
    customers = result.scalars().all()
    if q:
        needle = q.strip().lower()
        customers = [
            c
            for c in customers
            if needle in " ".join(filter(None, [c.full_name, c.company_name, c.email, c.phone])).lower()
        ]
    group = await _customer_data(session, [c.customer_id for c in customers])
    return [_customer_summary(c, *group[c.customer_id]) for c in customers]


@router.get("/portfolio/customers/{customer_id}", response_model=CustomerProfile)
async def customer_profile(customer_id: str, session: AsyncSession = Depends(get_db_session)) -> CustomerProfile:
    customer = await _get(session, Customer, customer_id, "CUSTOMER_NOT_FOUND")
    requests, policies, claims = (await _customer_data(session, [customer_id]))[customer_id]
    return CustomerProfile(
        customer=_customer_summary(customer, requests, policies, claims),
        notes=customer.notes,
        requests=[
            CustomerRequestBrief(
                request_id=r.request_id,
                request_number=r.request_number,
                insurance_type_label=catalog.line_label(r.insurance_type),
                product_type_label=catalog.product_label(r.insurance_type, r.product_type),
                status=r.status.value,
                created_at=_utc(r.created_at),
            )
            for r in requests
        ],
        policies=policies,
        claims=claims,
    )


@router.patch("/customers/{customer_id}", response_model=CustomerSummary)
async def update_customer(
    customer_id: str,
    body: CustomerUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(get_current_staff),
) -> CustomerSummary:
    customer = await _get(session, Customer, customer_id, "CUSTOMER_NOT_FOUND")
    changes = body.model_dump(exclude_unset=True)
    if "email" in changes and changes["email"]:
        changes["email"] = str(changes["email"])
    for key, value in changes.items():
        setattr(customer, key, value)
    audit(session, staff, "customer_updated", "customer", customer_id, fields=sorted(changes))
    await session.commit()
    requests, policies, claims = (await _customer_data(session, [customer_id]))[customer_id]
    return _customer_summary(customer, requests, policies, claims)


# --- Reports ---------------------------------------------------------------------


def _month(value: datetime) -> str:
    return value.strftime("%Y-%m")


@router.get("/reports/summary")
async def report_summary(
    date_from: date | None = None,
    date_to: date | None = None,
    session: AsyncSession = Depends(get_db_session),
    staff: SalesEmployee = Depends(require_roles(*UNDERWRITING)),
) -> dict:
    """Production, conversion, commissions, collections, claims and renewals for a period.

    Defaults to the last 12 months. Computed in Python from the live tables — fine for a
    brokerage's volumes; move to SQL aggregates if the book grows to many thousands of rows.
    """
    today = _today()
    end = date_to or today
    start = date_from or date(end.year - 1, end.month, 1)
    if start > end:
        raise _error(422, "INVALID_PERIOD", "date_from is after date_to")

    def in_period(value: datetime | None) -> bool:
        return value is not None and start <= _utc(value).date() <= end

    policies = await _policy_views(session, (await session.execute(select(InsurancePolicy))).scalars().all())
    requests = (await session.execute(select(InsuranceRequest))).scalars().all()
    claims = (await session.execute(select(InsuranceClaim))).scalars().all()
    installments = (await session.execute(select(PremiumInstallment))).scalars().all()

    issued = [p for p in policies if p.status != InsurancePolicyStatus.CANCELLED and in_period(p.issued_at)]
    period_requests = [r for r in requests if in_period(r.created_at)]
    converted = [r for r in period_requests if r.status == InsuranceRequestStatus.POLICY_ISSUED]

    by_line: dict[str, dict[str, Any]] = {}
    for line in catalog.LINES:
        by_line[line["key"]] = {
            "line": line["key"],
            "label": line["label"],
            "requests": 0,
            "policies": 0,
            "premium": ZERO,
            "commission": ZERO,
        }
    for r in period_requests:
        by_line.setdefault(
            r.insurance_type,
            {
                "line": r.insurance_type,
                "label": r.insurance_type,
                "requests": 0,
                "policies": 0,
                "premium": ZERO,
                "commission": ZERO,
            },
        )["requests"] += 1
    by_insurer: dict[str, dict[str, Any]] = {}
    for p in issued:
        row = by_line[p.insurance_type] if p.insurance_type in by_line else None
        if row:
            row["policies"] += 1
            row["premium"] += p.premium_amount or ZERO
            row["commission"] += p.commission_amount or ZERO
        ins = by_insurer.setdefault(
            p.insurer_id, {"insurer": p.insurer_name, "policies": 0, "premium": ZERO, "commission": ZERO}
        )
        ins["policies"] += 1
        ins["premium"] += p.premium_amount or ZERO
        ins["commission"] += p.commission_amount or ZERO

    months = []
    cursor = date(end.year, end.month, 1)
    for _ in range(12):
        months.append(cursor.strftime("%Y-%m"))
        cursor = date(cursor.year - (cursor.month == 1), (cursor.month - 2) % 12 + 1, 1)
    months.reverse()
    monthly: dict[str, dict[str, Any]] = {m: {"month": m, "premium": ZERO, "policies": 0} for m in months}
    for p in policies:
        if p.status != InsurancePolicyStatus.CANCELLED and p.issued_at and _month(p.issued_at) in monthly:
            monthly[_month(p.issued_at)]["premium"] += p.premium_amount or ZERO
            monthly[_month(p.issued_at)]["policies"] += 1

    due = [i for i in installments if i.status == InstallmentStatus.DUE]
    live = [p for p in policies if p.effective_status in ("issued", "active")]
    claim_status = Counter(c.status.value for c in claims)
    return {
        "period": {"from": start, "to": end},
        "premiums_written": sum((p.premium_amount or ZERO for p in issued), ZERO),
        "policies_issued": len(issued),
        "requests_created": len(period_requests),
        "conversion_rate": round(len(converted) / len(period_requests) * 100, 1) if period_requests else None,
        "commission_earned": sum((p.commission_amount or ZERO for p in issued), ZERO),
        "commission_received": sum(
            (p.commission_amount or ZERO for p in issued if p.commission_received_at), ZERO
        ),
        "commission_pending": sum(
            (
                p.commission_amount or ZERO
                for p in policies
                if p.status != InsurancePolicyStatus.CANCELLED
                and p.commission_amount
                and not p.commission_received_at
            ),
            ZERO,
        ),
        "by_line": [row for row in by_line.values() if row["requests"] or row["policies"]],
        "by_insurer": sorted(by_insurer.values(), key=lambda r: r["premium"], reverse=True),
        "monthly": list(monthly.values()),
        "request_status": Counter(r.status.value for r in requests),
        "collections": {
            "outstanding": sum((i.amount for i in due), ZERO),
            "overdue": sum((i.amount for i in due if i.due_date < today), ZERO),
            "collected_in_period": sum(
                (i.amount for i in installments if i.status == InstallmentStatus.PAID and in_period(i.paid_at)),
                ZERO,
            ),
        },
        "claims": {
            "by_status": claim_status,
            "open": sum(claim_status[s.value] for s in OPEN_CLAIMS),
            "approved_total": sum((c.approved_amount or ZERO for c in claims if c.approved_amount), ZERO),
            "paid_total": sum((c.paid_amount or ZERO for c in claims if c.paid_amount), ZERO),
        },
        "renewals": {
            f"due_{days}": len([p for p in live if p.days_to_expiry is not None and 0 <= p.days_to_expiry <= days])
            for days in (30, 60, 90)
        },
        "active_policies": len(live),
    }
