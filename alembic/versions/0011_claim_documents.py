"""Claim documents: link uploaded files to a claim.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-15

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("insurance_request_documents", sa.Column("claim_id", sa.String(length=36), nullable=True))
    op.create_index("ix_insurance_request_documents_claim_id", "insurance_request_documents", ["claim_id"])


def downgrade() -> None:
    op.drop_index("ix_insurance_request_documents_claim_id", table_name="insurance_request_documents")
    op.drop_column("insurance_request_documents", "claim_id")
