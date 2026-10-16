"""Required documents: automatic review results on each file, and signed authorization letters.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-15

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCS = "insurance_request_documents"


def upgrade() -> None:
    op.add_column(DOCS, sa.Column("review_status", sa.String(length=20), nullable=False, server_default="passed"))
    op.add_column(DOCS, sa.Column("review_findings", sa.Text(), nullable=True))
    op.add_column(DOCS, sa.Column("content_sha256", sa.String(length=64), nullable=True))
    op.add_column(DOCS, sa.Column("reviewed_by_id", sa.String(length=36), nullable=True))
    op.add_column(DOCS, sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(DOCS, sa.Column("rejection_reason", sa.String(length=500), nullable=True))

    op.create_table(
        "insurance_authorization_letters",
        sa.Column("letter_id", sa.String(length=36), primary_key=True),
        sa.Column("letter_number", sa.String(length=50), nullable=False, unique=True),
        sa.Column(
            "request_id", sa.String(length=36), sa.ForeignKey("insurance_requests.request_id"), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending_signature", "signed", "revoked", name="authorization_letter_status", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("issued_by_id", sa.String(length=36), nullable=True),
        sa.Column("signer_name", sa.String(length=200), nullable=True),
        sa.Column("signer_id_last4", sa.String(length=4), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_ip", sa.String(length=64), nullable=True),
        sa.Column("signed_user_agent", sa.String(length=255), nullable=True),
        sa.Column("signed_mobile", sa.String(length=32), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("verification_code", sa.String(length=16), nullable=False, unique=True),
        sa.Column("document_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_insurance_authorization_letters_request_id", "insurance_authorization_letters", ["request_id"]
    )


def downgrade() -> None:
    op.drop_table("insurance_authorization_letters")
    for column in ("rejection_reason", "reviewed_at", "reviewed_by_id", "content_sha256", "review_findings"):
        op.drop_column(DOCS, column)
    op.drop_column(DOCS, "review_status")
