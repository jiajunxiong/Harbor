"""Provider capability declaration tests."""

import unittest

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability, ProviderCapabilities


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
