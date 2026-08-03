"""Mock provider tests."""

import unittest
from datetime import date, timedelta

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


class MockProviderDailyQuotesTests(unittest.TestCase):
    """Verify the mock daily quotes generation contract."""

    def test_hk_daily_quotes_are_deterministic(self) -> None:
        provider = MockProvider()
        start, end = date(2026, 1, 5), date(2026, 1, 16)

        first = provider.fetch_daily_quotes(MarketTarget.HK, "0700.HK", start, end)
        second = provider.fetch_daily_quotes(MarketTarget.HK, "0700.HK", start, end)

        self.assertEqual(first, second)

    def test_daily_quotes_skip_weekends(self) -> None:
        start, end = date(2026, 1, 5), date(2026, 1, 16)
        rows = MockProvider().fetch_daily_quotes(MarketTarget.HK, "0700.HK", start, end)

        expected = sum(
            1
            for offset in range((end - start).days + 1)
            if (start + timedelta(days=offset)).weekday() < 5
        )
        self.assertEqual(len(rows), expected)
        for row in rows:
            self.assertLess(row["date"].weekday(), 5)

    def test_daily_quotes_rows_match_schema_and_ohlc_invariants(self) -> None:
        rows = MockProvider().fetch_daily_quotes(
            MarketTarget.US, "AAPL", date(2026, 1, 5), date(2026, 1, 9)
        )

        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(
                set(row),
                {
                    "market",
                    "symbol",
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "adjusted_close",
                    "source",
                },
            )
            self.assertEqual(row["market"], "US")
            self.assertEqual(row["symbol"], "AAPL")
            self.assertEqual(row["source"], "mock")
            self.assertGreaterEqual(row["high"], max(row["open"], row["close"]))
            self.assertLessEqual(row["low"], min(row["open"], row["close"]))
            self.assertGreater(row["volume"], 0)
            self.assertGreater(row["close"], 0)

    def test_hk_prices_are_positive(self) -> None:
        rows = MockProvider().fetch_daily_quotes(
            MarketTarget.HK, "0700.HK", date(2026, 1, 5), date(2026, 1, 9)
        )

        self.assertTrue(rows)
        for row in rows:
            self.assertGreater(row["open"], 0)
            self.assertGreater(row["close"], 0)

    def test_end_before_start_raises(self) -> None:
        provider = MockProvider()
        with self.assertRaises(ValueError):
            provider.fetch_daily_quotes(
                MarketTarget.HK,
                "0700.HK",
                date(2026, 1, 16),
                date(2026, 1, 5),
            )

    def test_both_target_raises(self) -> None:
        provider = MockProvider()
        with self.assertRaises(ValueError):
            provider.fetch_daily_quotes(
                MarketTarget.BOTH,
                "0700.HK",
                date(2026, 1, 5),
                date(2026, 1, 9),
            )
