"""FastAPI dependencies shared across route modules."""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session_factory
from app.core.market_context_cache import RedisLike
from app.core.redis_client import get_redis


async def get_db_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


def get_redis_client() -> RedisLike:
    return get_redis()
