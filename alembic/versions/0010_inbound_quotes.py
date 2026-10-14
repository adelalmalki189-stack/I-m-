"""Phase 4b: insurer replies mailbox and AI-extracted quotes awaiting review.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-15

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INBOUND_STATUSES = ("unmatched", "matched", "quote_created", "declined", "no_quote", "failed", "skipped")


def upgrade() -> None:
    op.add_column(
        "insurance_quotes", sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("insurance_quotes", sa.Column("inbound_email_id", sa.String(length=36), nullable=True))
    op.add_column("insurance_quotes", sa.Column("extraction_notes", sa.Text(), nullable=True))
    op.create_table(
        "automation_inbound_emails",
        sa.Column("inbound_id", sa.String(length=36), primary_key=True),
        sa.Column("message_id", sa.String(length=255), nullable=False, unique=True),
        sa.Column("from_address", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("attachments", sa.Text(), nullable=False),
        sa.Column("status", sa.Enum(*INBOUND_STATUSES, name="inbound_status", native_enum=False), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=True),
        sa.Column("submission_id", sa.String(length=36), nullable=True),
        sa.Column("quote_id", sa.String(length=36), nullable=True),
        sa.Column("extraction", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_automation_inbound_emails_status", "automation_inbound_emails", ["status"])
    op.create_index("ix_automation_inbound_emails_request_id", "automation_inbound_emails", ["request_id"])


def downgrade() -> None:
    op.drop_table("automation_inbound_emails")
    for column in ("extraction_notes", "inbound_email_id", "needs_review"):
        op.drop_column("insurance_quotes", column)
