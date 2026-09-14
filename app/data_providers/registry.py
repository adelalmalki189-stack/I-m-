"""Production DataProvider allow-list guard.

docs/TESTING_SPEC_V0.1.md §2 requires this enforced in code, not left to
discipline alone:

    - FakeDataProvider lives under tests/ only and is never imported here.
    - When settings.environment == "production", only classes explicitly
      registered via `register_production_provider` may be instantiated
      through this factory. Anything else — including an accidental import
      of a test double — raises before a single trade decision is made.
"""
from __future__ import annotations

from app.core.config import settings
from app.data_providers.base import DataProvider

_PRODUCTION_ALLOW_LIST: dict[str, type[DataProvider]] = {}


def register_production_provider(name: str, provider_cls: type[DataProvider]) -> None:
    _PRODUCTION_ALLOW_LIST[name] = provider_cls


def create_provider(name: str, *args: object, **kwargs: object) -> DataProvider:
    if settings.environment == "production" and name not in _PRODUCTION_ALLOW_LIST:
        raise RuntimeError(
            f"DataProvider '{name}' is not allow-listed for production. "
            f"Allowed: {sorted(_PRODUCTION_ALLOW_LIST)}"
        )

    provider_cls: type[DataProvider] | None = _PRODUCTION_ALLOW_LIST.get(name)
    if provider_cls is None:
        raise RuntimeError(f"Unknown DataProvider '{name}'")

    return provider_cls(*args, **kwargs)
