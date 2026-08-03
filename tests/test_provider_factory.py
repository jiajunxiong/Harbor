"""Provider factory tests."""

import unittest

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability
from harbor.infrastructure.data_providers.akshare import HKAKShareProvider
from harbor.infrastructure.data_providers.factory import create_provider
from harbor.infrastructure.data_providers.mock import MockProvider
from harbor.infrastructure.data_providers.yfinance import (
    HKYFinanceProvider,
    USYFinanceProvider,
)


class ProviderFactoryTests(unittest.TestCase):
    """Verify the market-scoped provider factory contract."""

    def test_mock_provider_for_hk(self) -> None:
        provider = create_provider(MarketTarget.HK, "mock")
        self.assertIsInstance(provider, MockProvider)

    def test_mock_provider_for_us(self) -> None:
        provider = create_provider(MarketTarget.US, "mock")
        self.assertIsInstance(provider, MockProvider)

    def test_mock_provider_declares_capabilities_for_both_markets(self) -> None:
        provider = create_provider(MarketTarget.HK, "mock")
        capabilities = provider.capabilities()
        self.assertTrue(capabilities.supports(MarketTarget.HK, Capability.DAILY_QUOTES))
        self.assertTrue(capabilities.supports(MarketTarget.US, Capability.DAILY_QUOTES))

    def test_akshare_provider_for_hk(self) -> None:
        provider = create_provider(MarketTarget.HK, "akshare")
        self.assertIsInstance(provider, HKAKShareProvider)

    def test_yfinance_provider_for_hk(self) -> None:
        provider = create_provider(MarketTarget.HK, "yfinance")
        self.assertIsInstance(provider, HKYFinanceProvider)

    def test_yfinance_provider_for_us(self) -> None:
        provider = create_provider(MarketTarget.US, "yfinance")
        self.assertIsInstance(provider, USYFinanceProvider)

    def test_provider_not_allowed_for_market_raises(self) -> None:
        with self.assertRaises(ValueError):
            create_provider(MarketTarget.HK, "wind")
