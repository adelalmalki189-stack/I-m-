"""Fictional demo data for development and staging (refuses to run in production).

    python -m app.cli seed-demo

Creates fictional insurers (example.com addresses), one staff account per department and customers with
requests at every stage, so every dashboard page has something to show.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.insurance import aftersales
from app.insurance.models import (
    ClaimStatus,
    Customer,
    InsuranceClaim,
    InsurancePolicy,
    InsurancePolicyStatus,
    InsuranceQuote,
    InsuranceRequest,
    InsuranceRequestStatus,
    InsuranceSubmission,
    InsuranceSubmissionStatus,
    Insurer,
    SalesEmployee,
)

DEMO_PASSWORD = "Demo-pass-2026"
INSURERS = [
    ("شركة التأمين التجريبية الأولى", "quotes@demo-insurer-one.example", "إدارة الاكتتاب"),
    ("شركة التأمين التجريبية الثانية", "rfq@demo-insurer-two.example", "قسم التسعير"),
    ("شركة التأمين التجريبية الثالثة", "quotes@demo-insurer-three.example", None),
]
STAFF = [
    ("سارة (تجريبي)", "sales@demo.bud.example", "sales_agent"),
    ("خالد (تجريبي)", "underwriter@demo.bud.example", "underwriter"),
    ("ليلى (تجريبي)", "claims@demo.bud.example", "claims_officer"),
    ("عبدالله (تجريبي)", "service@demo.bud.example", "customer_service"),
    ("منيرة (تجريبي)", "manager@demo.bud.example", "manager"),
]
CUSTOMERS = [
    # (name, company, mobile, line, product, details, coverage, stage)
    (
        "نورة (تجريبي)",
        None,
        "+966500000001",
        "motor",
        "motor_comprehensive",
        {
            "vehicle_make": "تويوتا",
            "vehicle_model": "كامري",
            "vehicle_year": 2023,
            "vehicle_count": 1,
            "vehicle_usage": "خاص",
        },
        "95000",
        "new",
    ),
    (
        "فهد (تجريبي)",
        "مؤسسة تجريبية للمقاولات",
        "+966500000002",
        "engineering",
        "eng_car",
        {
            "project_description": "مبنى سكني من 4 أدوار",
            "contract_value": 4500000,
            "duration_months": 14,
            "city": "الرياض",
        },
        "4500000",
        "needs_info",
    ),
    (
        "ريم (تجريبي)",
        "شركة تجريبية للتقنية",
        "+966500000003",
        "medical",
        "medical_group",
        {"members_count": 45, "network_class": "B", "dependents_included": "نعم", "start_date": "2026-12-01"},
        None,
        "ready_for_quote",
    ),
    (
        "ماجد (تجريبي)",
        None,
        "+966500000004",
        "property",
        "property_home",
        {"property_type": "سكني", "city": "جدة", "sum_insured": 1800000},
        "1800000",
        "policy",
    ),
    (
        "هند (تجريبي)",
        "مؤسسة تجريبية للتجارة",
        "+966500000005",
        "marine",
        "marine_cargo_open",
        {
            "goods_description": "مواد غذائية مبردة",
            "shipment_value": 2500000,
            "origin": "دبي",
            "destination": "الدمام",
            "transport_mode": "بحري",
        },
        "2500000",
        "policy_expiring",
    ),
]


class ProductionRefused(Exception):
    pass


async def seed_demo(session: AsyncSession, environment: str) -> dict[str, int]:
    if environment == "production":
        raise ProductionRefused("Demo data is never created in production")
    # Staff first, so re-running adds any new demo department to an already seeded database.
    for name, email, role in STAFF:
        if not (await session.execute(select(SalesEmployee).where(SalesEmployee.email == email))).first():
            session.add(
                SalesEmployee(full_name=name, email=email, role=role, password_hash=hash_password(DEMO_PASSWORD))
            )
    if (await session.execute(select(Customer).where(Customer.phone == CUSTOMERS[0][2]))).first():
        await session.commit()
        return {"created": 0}  # already seeded

    insurers = []
    for name, email, contact in INSURERS:
        existing = (await session.execute(select(Insurer).where(Insurer.email == email))).scalar_one_or_none()
        insurer = existing or Insurer(name=name, email=email, contact_name=contact)
        session.add(insurer)
        insurers.append(insurer)
    await session.flush()

    now = datetime.now(UTC)
    counts = {"customers": 0, "policies": 0, "claims": 0}
    for i, (name, company, mobile, line, product, details, coverage, stage) in enumerate(CUSTOMERS):
        customer = Customer(
            customer_type="business" if company else "individual",
            full_name=name,
            company_name=company,
            phone=mobile,
        )
        session.add(customer)
        await session.flush()
        counts["customers"] += 1
        status = {
            "new": InsuranceRequestStatus.NEW,
            "needs_info": InsuranceRequestStatus.NEEDS_INFO,
            "ready_for_quote": InsuranceRequestStatus.READY_FOR_QUOTE,
        }.get(stage, InsuranceRequestStatus.POLICY_ISSUED)
        request = InsuranceRequest(
            customer_id=customer.customer_id,
            request_number=f"IR-{now.strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}",
            insurance_type=line,
            product_type=product,
            request_source="portal",
            status=status,
            coverage_amount=coverage,
            request_details=json.dumps(details, ensure_ascii=False),
            preferred_insurers="[]",
            consent_at=now,
            last_status_change_at=now,
        )
        session.add(request)
        await session.flush()
        if stage == "ready_for_quote":
            for j, insurer in enumerate(insurers[:2]):
                sub = InsuranceSubmission(
                    request_id=request.request_id,
                    insurer_id=insurer.insurer_id,
                    category=line,
                    subject=f"طلب عرض سعر {request.request_number}",
                    email_body="(بيانات تجريبية)",
                    status=InsuranceSubmissionStatus.SENT,
                    approved_for_send=True,
                    sent_at=now - timedelta(days=1),
                )
                session.add(sub)
                await session.flush()
                session.add(
                    InsuranceQuote(
                        submission_id=sub.submission_id,
                        premium=str(52000 + j * 3500),
                        coverage_limit="500000",
                        currency="SAR",
                        commission_rate=Decimal("10") + j,
                        source_channel="email",
                    )
                )
        if stage in ("policy", "policy_expiring"):
            insurer = insurers[i % len(insurers)]
            sub = InsuranceSubmission(
                request_id=request.request_id,
                insurer_id=insurer.insurer_id,
                category=line,
                subject=f"طلب عرض سعر {request.request_number}",
                email_body="(بيانات تجريبية)",
                status=InsuranceSubmissionStatus.SENT,
                approved_for_send=True,
                sent_at=now - timedelta(days=40),
            )
            session.add(sub)
            await session.flush()
            premium = Decimal("6400.00") if stage == "policy" else Decimal("31000.00")
            quote = InsuranceQuote(
                submission_id=sub.submission_id,
                premium=str(premium),
                currency="SAR",
                commission_rate=Decimal("12.5"),
                source_channel="email",
            )
            session.add(quote)
            await session.flush()
            request.selected_quote_id = quote.quote_id
            start = now - timedelta(days=30 if stage == "policy" else 340)
            policy = InsurancePolicy(
                request_id=request.request_id,
                insurer_id=insurer.insurer_id,
                selected_quote_id=quote.quote_id,
                policy_number=f"POL-{now.strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}",
                status=InsurancePolicyStatus.ISSUED,
                coverage_amount=coverage,
                premium_amount=str(premium),
                currency="SAR",
                effective_from=start,
                effective_to=aftersales.one_year_term_end(start),
                issued_at=start,
                commission_rate=Decimal("12.5"),
                commission_amount=aftersales.commission_amount(premium, Decimal("12.5")),
            )
            session.add(policy)
            await session.flush()
            session.add_all(
                aftersales.build_installments(policy, premium, 4 if stage == "policy" else 1, start.date())
            )
            counts["policies"] += 1
            if stage == "policy":
                session.add(
                    InsuranceClaim(
                        claim_number=f"CL-{now.strftime('%Y%m%d')}-{uuid4().hex[:6].upper()}",
                        policy_id=policy.policy_id,
                        incident_date=date.today() - timedelta(days=3),
                        description="تسرب مياه في المطبخ (تجريبي)",
                        claimed_amount=Decimal("7500"),
                        status=ClaimStatus.REPORTED,
                        last_status_change_at=now,
                    )
                )
                counts["claims"] += 1
    await session.commit()
    return counts
