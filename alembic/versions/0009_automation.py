"""Phase 4 automation: customer message outbox, rule overrides, settings, opt-out.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-15

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.add_column(
        "insurance_customers",
        sa.Column("messaging_opt_out", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "automation_notifications",
        sa.Column("notification_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(length=36),
            sa.ForeignKey("insurance_customers.customer_id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("to_phone", sa.String(length=32), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("template_params", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "queued", "sent", "simulated", "failed", "skipped", name="notification_status", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.String(length=120), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dedupe_key", sa.String(length=160), nullable=True, unique=True),
        sa.Column("request_id", sa.String(length=36), nullable=True),
        sa.Column("policy_id", sa.String(length=36), nullable=True),
        sa.Column("claim_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_automation_notifications_customer_id", "automation_notifications", ["customer_id"])
    op.create_index("ix_automation_notifications_status", "automation_notifications", ["status"])
    op.create_table(
        "automation_rules",
        sa.Column("key", sa.String(length=50), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("whatsapp_template", sa.String(length=120), nullable=True),
        sa.Column("params", sa.Text(), nullable=True),
        *_timestamps(),
    )
    op.create_table(
        "automation_settings",
        sa.Column("key", sa.String(length=50), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        *_timestamps(),
    )


def downgrade() -> None:
    op.drop_table("automation_settings")
    op.drop_table("automation_rules")
    op.drop_table("automation_notifications")
    op.drop_column("insurance_customers", "messaging_opt_out")
