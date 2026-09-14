"""Async SQLAlchemy engine/session setup for PostgreSQL (docs/DATA_SPEC_V0.1.md).

Uses settings.database_url so tests can point this at an in-memory sqlite
database (via aiosqlite) without touching production wiring.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.database_url, echo=False, future=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def reset_engine_for_tests(database_url: str) -> AsyncEngine:
    """Rebind the module-level engine/session factory to a test database URL.

    Only ever called from tests/conftest.py — production code paths never
    call this, so a test database can never silently leak into a live run.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = create_async_engine(database_url, echo=False, future=True)
    _session_factory = async_sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine
