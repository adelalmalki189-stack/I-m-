"""Minimal in-memory stand-in for the subset of redis.asyncio.Redis this
project actually uses (get/set) — for tests only, matching RedisLike."""
from __future__ import annotations


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        del ex  # TTL not modeled — tests don't need expiry semantics
        self._store[key] = value
