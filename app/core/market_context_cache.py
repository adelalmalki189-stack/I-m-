"""Redis-backed cache for the latest computed Market Context.

docs/DATA_SPEC_V0.1.md §5: MarketContext lives in Redis (latest value) with
history embedded in signal_snapshots — there is no standalone SQL table.
Whatever live process eventually computes SPY/QQQ/VIX regimes (Phase 6+
scanner loop) publishes the full response shape here; the API layer
(app/api/routes/market.py) only ever reads it.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable
from typing import Any, Protocol

MARKET_CONTEXT_KEY = "market_context:latest"
DEFAULT_TTL_SECONDS = 30


class RedisLike(Protocol):
    """Structural subset of redis.asyncio.Redis this project uses. Declared
    with plain (non-async) methods returning Awaitable — matching how
    redis-py itself types these — so both the real client and FakeRedis
    (an `async def`, which returns the narrower Coroutine) satisfy it."""

    def get(self, key: str, /) -> Awaitable[str | bytes | None]: ...
    def set(self, key: str, value: str, /, ex: int | None = None) -> Awaitable[Any]: ...


async def get_cached_market_context(client: RedisLike) -> dict | None:
    raw = await client.get(MARKET_CONTEXT_KEY)
    if raw is None:
        return None
    return json.loads(raw)


async def set_cached_market_context(
    client: RedisLike, data: dict, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> None:
    await client.set(MARKET_CONTEXT_KEY, json.dumps(data), ex=ttl_seconds)
