"""ORM registry: importing this package registers every table on `Base.metadata`,
which is what Alembic autogenerate and the test-suite's `create_all` rely on.
"""
from app.insurance import models as _insurance_models  # noqa: F401 — registers insurance tables
from app.models.base import Base

__all__ = ["Base"]
