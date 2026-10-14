"""Phase 3 after-sales: claims, premium installments, commission rates, renewals.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.add_column("insurance_insurers", sa.Column("default_commission_rate", sa.Numeric(5, 2), nullable=True))
    op.add_column("insurance_policies", sa.Column("commission_rate", sa.Numeric(5, 2), nullable=True))
    op.add_column("insurance_policies", sa.Column("commission_amount", sa.Numeric(14, 2), nullable=True))
    op.add_column(
        "insurance_policies", sa.Column("commission_received_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("insurance_policies", sa.Column("commission_reference", sa.String(length=100), nullable=True))
    op.add_column("insurance_requests", sa.Column("renewal_of_policy_id", sa.String(length=36), nullable=True))
    op.create_index("ix_insurance_requests_renewal_of_policy_id", "insurance_requests", ["renewal_of_policy_id"])

    op.create_table(
        "insurance_commission_rates",
        sa.Column("rate_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "insurer_id", sa.String(length=36), sa.ForeignKey("insurance_insurers.insurer_id"), nullable=False
        ),
        sa.Column("line", sa.String(length=80), nullable=False),
        sa.Column("rate", sa.Numeric(5, 2), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("insurer_id", "line", name="uq_commission_rate_insurer_line"),
    )
    op.create_table(
        "insurance_premium_installments",
        sa.Column("installment_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "policy_id", sa.String(length=36), sa.ForeignKey("insurance_policies.policy_id"), nullable=False
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("due", "paid", "cancelled", name="installment_status", native_enum=False),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_method", sa.String(length=40), nullable=True),
        sa.Column("payment_reference", sa.String(length=100), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_insurance_premium_installments_policy_id", "insurance_premium_installments", ["policy_id"])
    op.create_table(
        "insurance_claims",
        sa.Column("claim_id", sa.String(length=36), primary_key=True),
        sa.Column("claim_number", sa.String(length=50), nullable=False, unique=True),
        sa.Column(
            "policy_id", sa.String(length=36), sa.ForeignKey("insurance_policies.policy_id"), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum(
                "reported",
                "submitted",
                "under_assessment",
                "approved",
                "rejected",
                "paid",
                "closed",
                name="claim_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("incident_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("claimed_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("approved_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("paid_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("insurer_reference", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_status_change_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_insurance_claims_policy_id", "insurance_claims", ["policy_id"])


def downgrade() -> None:
    op.drop_table("insurance_claims")
    op.drop_table("insurance_premium_installments")
    op.drop_table("insurance_commission_rates")
    op.drop_index("ix_insurance_requests_renewal_of_policy_id", table_name="insurance_requests")
    op.drop_column("insurance_requests", "renewal_of_policy_id")
    for column in ("commission_reference", "commission_received_at", "commission_amount", "commission_rate"):
        op.drop_column("insurance_policies", column)
    op.drop_column("insurance_insurers", "default_commission_rate")
