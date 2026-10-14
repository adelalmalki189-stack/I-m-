"""Insurance tables and columns that existed in the models but had no migration.

insurance_policies, insurance_request_preferences, insurance_underwriting_cases,
plus five insurance_requests columns used by the sales/underwriting flow.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('insurance_policies',
    sa.Column('policy_id', sa.String(length=36), nullable=False),
    sa.Column('request_id', sa.String(length=36), nullable=False),
    sa.Column('insurer_id', sa.String(length=36), nullable=False),
    sa.Column('selected_quote_id', sa.String(length=36), nullable=False),
    sa.Column('policy_number', sa.String(length=80), nullable=False),
    sa.Column('status', sa.Enum('draft', 'issued', 'active', 'expired', 'cancelled', name='insurance_policy_status', native_enum=False), nullable=False),
    sa.Column('coverage_amount', sa.String(length=50), nullable=True),
    sa.Column('premium_amount', sa.String(length=50), nullable=True),
    sa.Column('currency', sa.String(length=10), nullable=False),
    sa.Column('effective_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['insurer_id'], ['insurance_insurers.insurer_id'], ),
    sa.ForeignKeyConstraint(['request_id'], ['insurance_requests.request_id'], ),
    sa.PrimaryKeyConstraint('policy_id'),
    sa.UniqueConstraint('policy_number'),
    sa.UniqueConstraint('request_id')
    )
    op.create_table('insurance_request_preferences',
    sa.Column('preference_id', sa.String(length=36), nullable=False),
    sa.Column('request_id', sa.String(length=36), nullable=False),
    sa.Column('sales_employee_id', sa.String(length=36), nullable=False),
    sa.Column('preferred_insurers', sa.Text(), nullable=True),
    sa.Column('preferred_premium_range', sa.String(length=100), nullable=True),
    sa.Column('preferred_coverage_limit', sa.String(length=50), nullable=True),
    sa.Column('customer_priority', sa.String(length=30), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['request_id'], ['insurance_requests.request_id'], ),
    sa.ForeignKeyConstraint(['sales_employee_id'], ['insurance_sales_employees.employee_id'], ),
    sa.PrimaryKeyConstraint('preference_id')
    )
    op.create_table('insurance_underwriting_cases',
    sa.Column('case_id', sa.String(length=36), nullable=False),
    sa.Column('request_id', sa.String(length=36), nullable=False),
    sa.Column('sales_employee_id', sa.String(length=36), nullable=True),
    sa.Column('assigned_underwriter_id', sa.String(length=36), nullable=True),
    sa.Column('underwriting_status', sa.String(length=30), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['assigned_underwriter_id'], ['insurance_sales_employees.employee_id'], ),
    sa.ForeignKeyConstraint(['request_id'], ['insurance_requests.request_id'], ),
    sa.ForeignKeyConstraint(['sales_employee_id'], ['insurance_sales_employees.employee_id'], ),
    sa.PrimaryKeyConstraint('case_id'),
    sa.UniqueConstraint('request_id')
    )
    op.add_column('insurance_requests', sa.Column('preferred_premium_range', sa.String(length=100), nullable=True))
    op.add_column('insurance_requests', sa.Column('preferred_insurer', sa.String(length=200), nullable=True))
    op.add_column('insurance_requests', sa.Column('customer_preferences', sa.Text(), nullable=True))
    op.add_column('insurance_requests', sa.Column('sales_notes', sa.Text(), nullable=True))
    op.add_column('insurance_requests', sa.Column('selected_quote_id', sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column('insurance_requests', 'selected_quote_id')
    op.drop_column('insurance_requests', 'sales_notes')
    op.drop_column('insurance_requests', 'customer_preferences')
    op.drop_column('insurance_requests', 'preferred_insurer')
    op.drop_column('insurance_requests', 'preferred_premium_range')
    op.drop_table('insurance_underwriting_cases')
    op.drop_table('insurance_request_preferences')
    op.drop_table('insurance_policies')
