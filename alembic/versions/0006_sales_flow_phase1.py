"""Phase 1 sales flow: request lifecycle statuses, risk details, default templates,
delivery errors, and enum storage normalized to lowercase values.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_TABLES = ("insurance_requests", "insurance_submissions", "insurance_policies")


def upgrade() -> None:
    op.add_column("insurance_requests", sa.Column("request_details", sa.Text(), nullable=True))
    op.add_column("insurance_submissions", sa.Column("delivery_error", sa.Text(), nullable=True))
    with op.batch_alter_table("insurance_insurer_templates") as batch:
        batch.alter_column("insurer_id", existing_type=sa.String(length=36), nullable=True)

    # Earlier app versions persisted enum member names ("NEW"); the schema and the app
    # now both use values ("new").
    for table in ENUM_TABLES:
        op.execute(f"UPDATE {table} SET status = lower(status)")


def downgrade() -> None:
    op.execute("DELETE FROM insurance_insurer_templates WHERE insurer_id IS NULL")
    with op.batch_alter_table("insurance_insurer_templates") as batch:
        batch.alter_column("insurer_id", existing_type=sa.String(length=36), nullable=False)
    op.execute(
        "UPDATE insurance_requests SET status = 'closed' WHERE status IN ('quote_selected', 'policy_issued')"
    )
    op.execute("UPDATE insurance_submissions SET status = 'follow_up_required' WHERE status = 'failed'")
    op.drop_column("insurance_submissions", "delivery_error")
    op.drop_column("insurance_requests", "request_details")
