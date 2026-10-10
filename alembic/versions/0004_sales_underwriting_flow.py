"""Sales and underwriting workflow for insurance requests.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "insurance_sales_employees",
        sa.Column("employee_id", sa.String(length=36), primary_key=True),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=False, server_default="sales_agent"),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("branch", sa.String(length=120), nullable=True),
        sa.Column("manager_id", sa.String(length=36), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.add_column("insurance_requests", sa.Column("sales_employee_id", sa.String(length=36), nullable=True))
    op.add_column("insurance_requests", sa.Column("underwriter_id", sa.String(length=36), nullable=True))
    op.add_column("insurance_requests", sa.Column("underwriter_status", sa.String(length=30), nullable=False, server_default="new"))
    op.add_column("insurance_requests", sa.Column("preferred_insurers", sa.Text(), nullable=True, server_default="[]"))
    op.add_column("insurance_requests", sa.Column("premium_budget", sa.String(length=50), nullable=True))
    op.add_column("insurance_requests", sa.Column("coverage_limit", sa.String(length=50), nullable=True))
    op.add_column("insurance_requests", sa.Column("customer_priority", sa.String(length=30), nullable=True))
    op.create_foreign_key(
        "fk_insurance_requests_sales_employee",
        "insurance_requests",
        "insurance_sales_employees",
        ["sales_employee_id"],
        ["employee_id"],
    )
    op.create_foreign_key(
        "fk_insurance_requests_underwriter",
        "insurance_requests",
        "insurance_sales_employees",
        ["underwriter_id"],
        ["employee_id"],
    )

    op.add_column("insurance_request_documents", sa.Column("uploaded_by_employee_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_insurance_documents_uploaded_by",
        "insurance_request_documents",
        "insurance_sales_employees",
        ["uploaded_by_employee_id"],
        ["employee_id"],
    )

    op.create_table(
        "insurance_underwriting_assignments",
        sa.Column("assignment_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(length=36),
            sa.ForeignKey("insurance_requests.request_id"),
            nullable=False,
        ),
        sa.Column(
            "assigned_by_employee_id",
            sa.String(length=36),
            sa.ForeignKey("insurance_sales_employees.employee_id"),
            nullable=True,
        ),
        sa.Column(
            "assigned_to_employee_id",
            sa.String(length=36),
            sa.ForeignKey("insurance_sales_employees.employee_id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="assigned"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("insurance_underwriting_assignments")
    op.drop_constraint("fk_insurance_documents_uploaded_by", "insurance_request_documents", type_="foreignkey")
    op.drop_column("insurance_request_documents", "uploaded_by_employee_id")
    op.drop_constraint("fk_insurance_requests_underwriter", "insurance_requests", type_="foreignkey")
    op.drop_constraint("fk_insurance_requests_sales_employee", "insurance_requests", type_="foreignkey")
    op.drop_column("insurance_requests", "customer_priority")
    op.drop_column("insurance_requests", "coverage_limit")
    op.drop_column("insurance_requests", "premium_budget")
    op.drop_column("insurance_requests", "preferred_insurers")
    op.drop_column("insurance_requests", "underwriter_status")
    op.drop_column("insurance_requests", "underwriter_id")
    op.drop_column("insurance_requests", "sales_employee_id")
    op.drop_table("insurance_sales_employees")
