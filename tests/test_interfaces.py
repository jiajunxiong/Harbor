"""Provider capability declaration and interface tests."""

import inspect
import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.interfaces import (
    Capability,
    MarketDataProvider,
    ProviderCapabilities,
)


class StubProvider(MarketDataProvider):
    """A minimal provider used to exercise the base interface."""

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            {
                MarketTarget.HK: frozenset({Capability.DAILY_QUOTES, Capability.DIVIDENDS}),
            }
        )


class CapabilityTests(unittest.TestCase):
    """Verify the provider capability declaration contract."""

    def test_capability_members_cover_required_data_types(self) -> None:
        self.assertEqual(
            set(Capability),
            {
                Capability.DAILY_QUOTES,
                Capability.DIVIDENDS,
                Capability.FUNDAMENTALS,
                Capability.CORPORATE_ACTIONS,
                Capability.ADJUSTED_FACTORS,
            },
        )

    def test_supports_is_market_scoped(self) -> None:
        capabilities = ProviderCapabilities(
            {
                MarketTarget.HK: frozenset({Capability.DAILY_QUOTES, Capability.DIVIDENDS}),
            }
        )

        self.assertTrue(capabilities.supports(MarketTarget.HK, Capability.DAILY_QUOTES))
        self.assertFalse(capabilities.supports(MarketTarget.US, Capability.DAILY_QUOTES))
        self.assertFalse(capabilities.supports(MarketTarget.HK, Capability.FUNDAMENTALS))

    def test_markets_returns_declared_markets(self) -> None:
        capabilities = ProviderCapabilities(
            {
                MarketTarget.HK: frozenset({Capability.DAILY_QUOTES}),
                MarketTarget.US: frozenset({Capability.ADJUSTED_FACTORS}),
            }
        )

        self.assertEqual(set(capabilities.markets()), {MarketTarget.HK, MarketTarget.US})


class MarketDataProviderTests(unittest.TestCase):
    """Verify the unified provider interface contract."""

    def test_provider_is_abstract(self) -> None:
        with self.assertRaises(TypeError):
            MarketDataProvider()  # type: ignore[abstract]

    def test_every_data_method_accepts_market(self) -> None:
        methods = [
            member
            for name, member in inspect.getmembers(MarketDataProvider, inspect.isfunction)
            if not name.startswith("_") and name != "capabilities"
        ]
        self.assertTrue(methods)
        for method in methods:
            parameters = inspect.signature(method).parameters
            self.assertIn("market", parameters, msg=method.__name__)

    def test_unsupported_fetch_raises_not_implemented(self) -> None:
        provider = StubProvider()
        with self.assertRaises(NotImplementedError):
            provider.fetch_corporate_actions(
                MarketTarget.HK,
                "0005.HK",
                date(2026, 1, 1),
                date(2026, 12, 31),
            )

    def test_capabilities_are_declared_per_market(self) -> None:
        provider = StubProvider()
        capabilities = provider.capabilities()
        self.assertTrue(capabilities.supports(MarketTarget.HK, Capability.DAILY_QUOTES))
        self.assertFalse(capabilities.supports(MarketTarget.US, Capability.DAILY_QUOTES))
