"""Insurance Phase 1 schema — customers, requests, and document metadata.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "insurance_customers",
        sa.Column("customer_id", sa.String(length=36), primary_key=True),
        sa.Column("customer_type", sa.String(length=20), nullable=False, server_default="individual"),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("company_name", sa.String(length=200), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "insurance_requests",
        sa.Column("request_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(length=36),
            sa.ForeignKey("insurance_customers.customer_id"),
            nullable=False,
        ),
        sa.Column("request_number", sa.String(length=50), nullable=False, unique=True),
        sa.Column("insurance_type", sa.String(length=80), nullable=False),
        sa.Column("product_type", sa.String(length=80), nullable=False),
        sa.Column("request_source", sa.String(length=50), nullable=False, server_default="portal"),
        sa.Column(
            "status",
            sa.Enum(
                "new",
                "needs_info",
                "under_review",
                "ready_for_quote",
                "closed",
                name="insurance_request_status",
                native_enum=False,
            ),
            nullable=False,
            server_default="new",
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("coverage_amount", sa.String(length=50), nullable=True),
        sa.Column("last_status_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "insurance_request_documents",
        sa.Column("document_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(length=36),
            sa.ForeignKey("insurance_requests.request_id"),
            nullable=False,
        ),
        sa.Column("document_type", sa.String(length=80), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False, server_default="application/octet-stream"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("insurance_request_documents")
    op.drop_table("insurance_requests")
    op.drop_table("insurance_customers")
