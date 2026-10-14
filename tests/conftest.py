from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_staff
from app.api.deps import get_db_session
from app.api.routes import auth as auth_routes
from app.api.routes import insurance as insurance_routes
from app.core import database
from app.insurance.models import SalesEmployee
from app.main import app
from app.models import Base

TEST_MANAGER_EMAIL = "test.manager@bud.example"


def pytest_configure(config):
    config.addinivalue_line("markers", "real_auth: use real login instead of the auto-logged-in test manager")


@pytest_asyncio.fixture
async def engine():
    eng = await database.reset_engine_for_tests("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine):
    factory = database.get_session_factory()
    async with factory() as session:
        yield session


async def _test_manager(session: AsyncSession = Depends(get_db_session)) -> SalesEmployee:
    result = await session.execute(select(SalesEmployee).where(SalesEmployee.email == TEST_MANAGER_EMAIL))
    manager = result.scalar_one_or_none()
    if manager is None:
        manager = SalesEmployee(full_name="مدير الاختبار", email=TEST_MANAGER_EMAIL, role="manager")
        session.add(manager)
        await session.flush()
    return manager


@pytest.fixture(autouse=True)
def staff_auth(request):
    """API tests act as a logged-in manager unless marked `real_auth`."""
    auth_routes.login_limiter.clear()
    insurance_routes.portal_limiter.clear()
    if request.node.get_closest_marker("real_auth"):
        yield
        return
    app.dependency_overrides[get_current_staff] = _test_manager
    yield
    app.dependency_overrides.pop(get_current_staff, None)
