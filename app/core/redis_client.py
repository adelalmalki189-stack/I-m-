"""Thin async Redis wrapper for live/latest-value cache.

Per docs/DATA_SPEC_V0.1.md, Redis holds only the *latest* value for fast
reads (underlying_quotes, option_quotes, market_context, the newest row of
technical_features) — history lives in Postgres. This module intentionally
exposes only get/set/ping so callers can't accidentally treat Redis as a
system of record.

docs/OPERATIONAL_SPEC_V0.1.md §17 (from the original spec): the system must
keep working in degraded mode if Redis is unavailable — `ping()` is what
callers use to detect that and fall back rather than crash.
"""
from __future__ import annotations

import redis.asyncio as redis

from app.core.config import settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def is_available() -> bool:
    try:
        return bool(await get_redis().ping())
    except (redis.RedisError, OSError):
        return False


async def set_json(key: str, value: str, ttl_seconds: int | None = None) -> None:
    await get_redis().set(key, value, ex=ttl_seconds)


async def get_json(key: str) -> str | None:
    # decode_responses=True guarantees str at runtime; the redis-py stubs
    # aren't parameterized on that flag, so the cast just tells mypy what
    # we already know.
    value = await get_redis().get(key)
    return value if value is None else str(value)
