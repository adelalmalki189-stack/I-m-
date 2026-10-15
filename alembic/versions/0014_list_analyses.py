"""Underwriting analysis of Excel lists (census, fleet, assets) and their formatted output.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-15

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "insurance_list_analyses",
        sa.Column("analysis_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "request_id", sa.String(length=36), sa.ForeignKey("insurance_requests.request_id"), nullable=False
        ),
        sa.Column("document_id", sa.String(length=36), nullable=False, unique=True),
        sa.Column("profile", sa.String(length=20), nullable=False),
        sa.Column("sheet_name", sa.String(length=100), nullable=True),
        sa.Column("options", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("issue_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("output_document_id", sa.String(length=36), nullable=True),
        sa.Column("analyzed_by_id", sa.String(length=36), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_insurance_list_analyses_request_id", "insurance_list_analyses", ["request_id"])


def downgrade() -> None:
    op.drop_table("insurance_list_analyses")
