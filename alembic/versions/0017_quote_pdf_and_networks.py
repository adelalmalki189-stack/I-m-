"""Quotes read from insurers' PDFs (deductible, network class, source file) and medical networks.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("insurance_quotes", sa.Column("deductible", sa.String(length=300), nullable=True))
    op.add_column("insurance_quotes", sa.Column("network_class", sa.String(length=40), nullable=True))
    op.add_column("insurance_quotes", sa.Column("source_document_id", sa.String(length=36), nullable=True))
    op.create_table(
        "insurance_network_providers",
        sa.Column("provider_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "insurer_id", sa.String(length=36), sa.ForeignKey("insurance_insurers.insurer_id"), nullable=False
        ),
        sa.Column("network_class", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("provider_type", sa.String(length=80), nullable=True),
        sa.Column("region", sa.String(length=120), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_insurance_network_providers_insurer_id", "insurance_network_providers", ["insurer_id"])
    op.create_index(
        "ix_insurance_network_providers_network_class", "insurance_network_providers", ["network_class"]
    )


def downgrade() -> None:
    op.drop_table("insurance_network_providers")
    for column in ("source_document_id", "network_class", "deductible"):
        op.drop_column("insurance_quotes", column)
