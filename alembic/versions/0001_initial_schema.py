"""Base revision (intentionally empty).

This revision originally created the options-trading tables, which were
removed when the project became BUD-only. It is kept as an empty root so the
revision chain (0002 → …) stays valid for databases already stamped at 0001.

Revision ID: 0001
Revises:
Create Date: 2026-09-14

"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
