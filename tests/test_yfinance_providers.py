"""yfinance provider tests."""

import unittest

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability
from harbor.infrastructure.data_providers.yfinance import (
    HKYFinanceProvider,
    USYFinanceProvider,
)


class YFinanceProviderTests(unittest.TestCase):
    """Verify the yfinance client wrapper contracts."""

    def test_hk_provider_normalizes_symbol_with_hk_suffix(self) -> None:
        provider = HKYFinanceProvider()

        self.assertEqual(provider._normalize_symbol("0700"), "0700.HK")
        self.assertEqual(provider._normalize_symbol("0700.HK"), "0700.HK")

    def test_hk_provider_serves_hk_market_only(self) -> None:
        capabilities = HKYFinanceProvider().capabilities()

        self.assertTrue(capabilities.supports(MarketTarget.HK, Capability.DAILY_QUOTES))
        self.assertFalse(capabilities.supports(MarketTarget.US, Capability.DAILY_QUOTES))

    def test_us_provider_uses_symbol_directly(self) -> None:
        provider = USYFinanceProvider()

        self.assertEqual(provider._normalize_symbol("aapl"), "AAPL")
        self.assertEqual(provider._normalize_symbol(" AAPL "), "AAPL")

    def test_us_provider_serves_us_market_only(self) -> None:
        capabilities = USYFinanceProvider().capabilities()

        self.assertTrue(capabilities.supports(MarketTarget.US, Capability.DAILY_QUOTES))
        self.assertFalse(capabilities.supports(MarketTarget.HK, Capability.DAILY_QUOTES))
