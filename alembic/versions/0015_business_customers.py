"""Businesses only: CR and VAT numbers on customers, the filer's contact on each request,
and the signatory's position on authorization letters.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-15

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("insurance_customers", sa.Column("cr_number", sa.String(length=10), nullable=True))
    op.add_column("insurance_customers", sa.Column("vat_number", sa.String(length=15), nullable=True))
    op.add_column("insurance_customers", sa.Column("contact_title", sa.String(length=120), nullable=True))
    op.create_index("ix_insurance_customers_cr_number", "insurance_customers", ["cr_number"])
    op.add_column("insurance_requests", sa.Column("contact_name", sa.String(length=200), nullable=True))
    op.add_column("insurance_requests", sa.Column("contact_mobile", sa.String(length=32), nullable=True))
    op.add_column("insurance_requests", sa.Column("contact_title", sa.String(length=120), nullable=True))
    op.add_column(
        "insurance_authorization_letters", sa.Column("signer_title", sa.String(length=120), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("insurance_authorization_letters", "signer_title")
    for column in ("contact_title", "contact_mobile", "contact_name"):
        op.drop_column("insurance_requests", column)
    op.drop_index("ix_insurance_customers_cr_number", table_name="insurance_customers")
    for column in ("contact_title", "vat_number", "cr_number"):
        op.drop_column("insurance_customers", column)
