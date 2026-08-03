"""Provider factory tests."""

import io
import unittest

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability
from harbor.infrastructure.data_providers.akshare import HKAKShareProvider
from harbor.infrastructure.data_providers.factory import (
    capability_report,
    create_provider,
    print_capability_report,
)
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


class CapabilityReportTests(unittest.TestCase):
    """Verify the data provider capability report."""

    def test_report_covers_all_registered_providers(self) -> None:
        report = capability_report()
        providers = {entry["provider"] for entry in report}
        self.assertEqual(providers, {"mock", "yfinance", "akshare"})

    def test_report_lists_markets_per_provider(self) -> None:
        report = capability_report()
        by_provider = {entry["provider"]: entry for entry in report}

        self.assertEqual(by_provider["mock"]["markets"], ["HK", "US"])
        self.assertEqual(by_provider["akshare"]["markets"], ["HK"])
        self.assertIn("HK", by_provider["yfinance"]["markets"])
        self.assertIn("US", by_provider["yfinance"]["markets"])

    def test_report_lists_capabilities_per_market(self) -> None:
        report = capability_report()
        mock_entry = next(entry for entry in report if entry["provider"] == "mock")

        hk_capabilities = mock_entry["capabilities"]["HK"]
        self.assertIn("daily_quotes", hk_capabilities)
        self.assertIn("corporate_actions", hk_capabilities)

    def test_print_capability_report_writes_to_stream(self) -> None:
        output = io.StringIO()
        print_capability_report(output)

        text = output.getvalue()
        self.assertIn("provider=mock", text)
        self.assertIn("provider=yfinance", text)
        self.assertIn("provider=akshare", text)
        self.assertIn("HK", text)
