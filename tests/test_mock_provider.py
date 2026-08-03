"""Mock provider securities tests."""

import unittest

from harbor.config import MarketTarget
from harbor.infrastructure.data_providers.mock import MockProvider


class MockProviderSecuritiesTests(unittest.TestCase):
    """Verify the mock securities universe contract."""

    def test_hk_securities_end_with_hk_and_within_range(self) -> None:
        rows = MockProvider().list_securities(MarketTarget.HK)

        self.assertGreaterEqual(len(rows), 10)
        self.assertLessEqual(len(rows), 20)
        for row in rows:
            self.assertEqual(row["market"], "HK")
            self.assertTrue(row["symbol"].endswith(".HK"))
            self.assertEqual(row["exchange"], "HKEX")
            self.assertTrue(row["is_active"])
            self.assertIsNone(row["delist_date"])

    def test_us_securities_include_known_tickers_and_within_range(self) -> None:
        rows = MockProvider().list_securities(MarketTarget.US)

        self.assertGreaterEqual(len(rows), 10)
        self.assertLessEqual(len(rows), 20)
        symbols = {row["symbol"] for row in rows}
        self.assertIn("AAPL", symbols)
        self.assertIn("MSFT", symbols)
        for row in rows:
            self.assertEqual(row["market"], "US")
            self.assertFalse(row["symbol"].endswith(".HK"))

    def test_securities_rows_match_securities_schema(self) -> None:
        for row in MockProvider().list_securities(MarketTarget.HK):
            self.assertEqual(
                set(row),
                {
                    "market",
                    "symbol",
                    "name",
                    "exchange",
                    "list_date",
                    "delist_date",
                    "is_active",
                },
            )

    def test_list_securities_rejects_both_target(self) -> None:
        provider = MockProvider()
        with self.assertRaises(ValueError):
            provider.list_securities(MarketTarget.BOTH)
