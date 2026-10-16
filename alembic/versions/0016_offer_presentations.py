"""Offers shown to the customer and their signed acceptance.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-15

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "insurance_offer_presentations",
        sa.Column("presentation_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "request_id", sa.String(length=36), sa.ForeignKey("insurance_requests.request_id"), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum("published", "accepted", "withdrawn", name="offer_presentation_status", native_enum=False),
            nullable=False,
        ),
        sa.Column("quote_ids", sa.Text(), nullable=False),
        sa.Column("recommended_quote_id", sa.String(length=36), nullable=True),
        sa.Column("note_to_customer", sa.Text(), nullable=True),
        sa.Column("published_by_id", sa.String(length=36), nullable=True),
        sa.Column("customer_comments", sa.Text(), nullable=True),
        sa.Column("accepted_quote_id", sa.String(length=36), nullable=True),
        sa.Column("acceptance_text", sa.Text(), nullable=True),
        sa.Column("signer_name", sa.String(length=200), nullable=True),
        sa.Column("signer_id_last4", sa.String(length=4), nullable=True),
        sa.Column("signer_title", sa.String(length=120), nullable=True),
        sa.Column("signed_mobile", sa.String(length=32), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_ip", sa.String(length=64), nullable=True),
        sa.Column("signed_user_agent", sa.String(length=255), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("verification_code", sa.String(length=16), nullable=False, unique=True),
        sa.Column("document_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_insurance_offer_presentations_request_id", "insurance_offer_presentations", ["request_id"]
    )


def downgrade() -> None:
    op.drop_table("insurance_offer_presentations")
