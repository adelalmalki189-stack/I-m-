"""Insurance email workflow — insurer profiles, templates, submissions, quotes, and audit log.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "insurance_insurers",
        sa.Column("insurer_id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("contact_name", sa.String(length=200), nullable=True),
        sa.Column(
            "communication_channel",
            sa.String(length=30),
            nullable=False,
            server_default="email",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "insurance_insurer_templates",
        sa.Column("template_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "insurer_id",
            sa.String(length=36),
            sa.ForeignKey("insurance_insurers.insurer_id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("subject_template", sa.String(length=255), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column("required_attachments", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "insurance_submissions",
        sa.Column("submission_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(length=36),
            sa.ForeignKey("insurance_requests.request_id"),
            nullable=False,
        ),
        sa.Column(
            "insurer_id",
            sa.String(length=36),
            sa.ForeignKey("insurance_insurers.insurer_id"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email_body", sa.Text(), nullable=False),
        sa.Column(
            "communication_channel",
            sa.String(length=30),
            nullable=False,
            server_default="email",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "pending_approval",
                "approved",
                "sent",
                "follow_up_required",
                "rejected",
                "closed",
                name="insurance_submission_status",
                native_enum=False,
            ),
            nullable=False,
            server_default="pending_approval",
        ),
        sa.Column("approved_for_send", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_follow_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "insurance_quotes",
        sa.Column("quote_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "submission_id",
            sa.String(length=36),
            sa.ForeignKey("insurance_submissions.submission_id"),
            nullable=False,
        ),
        sa.Column("premium", sa.String(length=50), nullable=True),
        sa.Column("coverage_limit", sa.String(length=50), nullable=True),
        sa.Column("exclusions", sa.Text(), nullable=True),
        sa.Column("conditions", sa.Text(), nullable=True),
        sa.Column("validity_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("source_channel", sa.String(length=30), nullable=False, server_default="email"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "insurance_email_audit_logs",
        sa.Column("audit_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "submission_id",
            sa.String(length=36),
            sa.ForeignKey("insurance_submissions.submission_id"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("actor_name", sa.String(length=200), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("insurance_email_audit_logs")
    op.drop_table("insurance_quotes")
    op.drop_table("insurance_submissions")
    op.drop_table("insurance_insurer_templates")
    op.drop_table("insurance_insurers")
