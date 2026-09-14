"""Enforces docs/TESTING_SPEC_V0.1.md §2 — "No Mock Data in Production" as a
CI-checked rule, not just a convention.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.core.config import settings
from app.data_providers import registry
from app.data_providers.base import DataProvider

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"


def _imports_fake_provider(path: pathlib.Path) -> bool:
    """True only for an actual `import`/`from ... import` of the fake
    provider or the `tests` package — mentioning the name in a comment or
    docstring (as this module's own file does) does not count."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "tests" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] == "tests" or any(
                alias.name == "FakeDataProvider" for alias in node.names
            ):
                return True
    return False


def test_fake_data_provider_never_referenced_under_app():
    offending = [str(path) for path in APP_DIR.rglob("*.py") if _imports_fake_provider(path)]
    assert not offending, f"FakeDataProvider imported under app/: {offending}"


def test_registry_rejects_unregistered_provider_in_production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(RuntimeError):
        registry.create_provider("NOT_REGISTERED")


def test_registry_allows_explicitly_registered_provider_in_production(monkeypatch):
    class _DummyProvider(DataProvider):
        async def connect(self) -> None: ...
        async def disconnect(self) -> None: ...

        @property
        def is_connected(self) -> bool:
            return True

        async def get_historical_candles_5m(self, symbol, start, end):
            return []

        async def subscribe_candles_5m(self, symbol, callback):
            ...

        async def get_underlying_quote(self, symbol):
            raise NotImplementedError

        async def get_option_chain(self, symbol):
            return []

        async def get_option_quote(self, contract_id):
            raise NotImplementedError

        async def subscribe_option_quote(self, contract_id, callback):
            ...

        async def get_market_context(self):
            raise NotImplementedError

    registry.register_production_provider("DUMMY_FOR_TEST", _DummyProvider)
    monkeypatch.setattr(settings, "environment", "production")

    provider = registry.create_provider("DUMMY_FOR_TEST")
    assert isinstance(provider, _DummyProvider)
