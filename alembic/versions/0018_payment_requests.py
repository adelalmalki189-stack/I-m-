"""Payment requests before issuing: amount, bank details, transfer proof, confirmation.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "insurance_payment_requests",
        sa.Column("payment_id", sa.String(length=36), primary_key=True),
        sa.Column("payment_number", sa.String(length=30), nullable=False, unique=True),
        sa.Column(
            "request_id", sa.String(length=36), sa.ForeignKey("insurance_requests.request_id"), nullable=False
        ),
        sa.Column("quote_id", sa.String(length=36), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "issued", "proof_submitted", "paid", "cancelled", name="payment_request_status", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column("total_premium", sa.Numeric(14, 2), nullable=False),
        sa.Column("vat_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("installments", sa.Integer(), nullable=False),
        sa.Column("amount_due", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("bank_name", sa.String(length=120), nullable=True),
        sa.Column("account_name", sa.String(length=200), nullable=True),
        sa.Column("iban", sa.String(length=34), nullable=True),
        sa.Column("issued_by_id", sa.String(length=36), nullable=True),
        sa.Column("document_id", sa.String(length=36), nullable=True),
        sa.Column("proof_document_id", sa.String(length=36), nullable=True),
        sa.Column("proof_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proof_rejected_reason", sa.String(length=500), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_method", sa.String(length=40), nullable=True),
        sa.Column("bank_reference", sa.String(length=100), nullable=True),
        sa.Column("confirmed_by_id", sa.String(length=36), nullable=True),
        sa.Column("receipt_document_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_insurance_payment_requests_request_id", "insurance_payment_requests", ["request_id"])


def downgrade() -> None:
    op.drop_table("insurance_payment_requests")
