from __future__ import annotations

import pytest_asyncio

from app.core import database
from app.models import Base


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
