"""Phase 2: staff login (password hash, sessions), audit trail, portal consent and upload tokens.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPS = (
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
)


def upgrade() -> None:
    op.add_column("insurance_sales_employees", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column(
        "insurance_sales_employees", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("insurance_requests", sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("insurance_requests", sa.Column("upload_token_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "insurance_requests", sa.Column("upload_token_expires_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "insurance_staff_sessions",
        sa.Column("session_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "employee_id",
            sa.String(length=36),
            sa.ForeignKey("insurance_sales_employees.employee_id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        *(c.copy() for c in TIMESTAMPS),
    )
    op.create_index("ix_insurance_staff_sessions_employee_id", "insurance_staff_sessions", ["employee_id"])

    op.create_table(
        "insurance_audit_events",
        sa.Column("event_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "actor_id",
            sa.String(length=36),
            sa.ForeignKey("insurance_sales_employees.employee_id"),
            nullable=True,
        ),
        sa.Column("actor_name", sa.String(length=200), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        *(c.copy() for c in TIMESTAMPS),
    )
    op.create_index("ix_insurance_audit_events_action", "insurance_audit_events", ["action"])
    op.create_index("ix_insurance_audit_events_entity_id", "insurance_audit_events", ["entity_id"])


def downgrade() -> None:
    op.drop_table("insurance_audit_events")
    op.drop_table("insurance_staff_sessions")
    op.drop_column("insurance_requests", "upload_token_expires_at")
    op.drop_column("insurance_requests", "upload_token_hash")
    op.drop_column("insurance_requests", "consent_at")
    op.drop_column("insurance_sales_employees", "last_login_at")
    op.drop_column("insurance_sales_employees", "password_hash")
