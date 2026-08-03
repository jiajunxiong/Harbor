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


class MockProviderDividendsTests(unittest.TestCase):
    """Verify the mock dividends generation contract."""

    def test_hk_dividends_are_deterministic(self) -> None:
        provider = MockProvider()
        start, end = date(2025, 1, 1), date(2026, 12, 31)

        first = provider.fetch_dividends(MarketTarget.HK, "0700.HK", start, end)
        second = provider.fetch_dividends(MarketTarget.HK, "0700.HK", start, end)

        self.assertEqual(first, second)

    def test_hk_dividends_rows_match_schema_and_consistency(self) -> None:
        rows = MockProvider().fetch_dividends(
            MarketTarget.HK, "0700.HK", date(2025, 1, 1), date(2026, 12, 31)
        )

        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(
                set(row),
                {
                    "market",
                    "symbol",
                    "ex_date",
                    "record_date",
                    "payment_date",
                    "amount",
                    "type",
                    "currency",
                },
            )
            self.assertEqual(row["market"], "HK")
            self.assertEqual(row["symbol"], "0700.HK")
            self.assertEqual(row["currency"], "HKD")
            self.assertIn(row["type"], {"regular", "special"})
            self.assertGreater(row["amount"], 0)
            self.assertLess(row["ex_date"], row["record_date"])
            self.assertLess(row["record_date"], row["payment_date"])

    def test_hk_dividends_include_regular_and_special(self) -> None:
        rows = MockProvider().fetch_dividends(
            MarketTarget.HK, "0700.HK", date(2020, 1, 1), date(2026, 12, 31)
        )

        types = {row["type"] for row in rows}
        self.assertIn("regular", types)
        self.assertIn("special", types)

    def test_us_dividends_currency_is_usd(self) -> None:
        rows = MockProvider().fetch_dividends(
            MarketTarget.US, "AAPL", date(2025, 1, 1), date(2026, 12, 31)
        )

        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["currency"], "USD")
            self.assertIn(row["type"], {"regular", "special"})

    def test_end_before_start_raises(self) -> None:
        provider = MockProvider()
        with self.assertRaises(ValueError):
            provider.fetch_dividends(
                MarketTarget.HK,
                "0700.HK",
                date(2026, 12, 31),
                date(2026, 1, 1),
            )

    def test_both_target_raises(self) -> None:
        provider = MockProvider()
        with self.assertRaises(ValueError):
            provider.fetch_dividends(
                MarketTarget.BOTH,
                "0700.HK",
                date(2026, 1, 1),
                date(2026, 12, 31),
            )


class MockProviderFinancialsTests(unittest.TestCase):
    """Verify the mock financials generation contract."""

    def test_hk_financials_are_deterministic(self) -> None:
        provider = MockProvider()

        first = provider.fetch_financials(MarketTarget.HK, "0700.HK")
        second = provider.fetch_financials(MarketTarget.HK, "0700.HK")

        self.assertEqual(first, second)

    def test_financials_rows_match_schema_and_consistency(self) -> None:
        rows = MockProvider().fetch_financials(MarketTarget.HK, "0700.HK")

        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(
                set(row),
                {
                    "market",
                    "symbol",
                    "report_date",
                    "fiscal_period",
                    "roe",
                    "net_income",
                    "total_equity",
                    "revenue",
                },
            )
            self.assertEqual(row["market"], "HK")
            self.assertEqual(row["symbol"], "0700.HK")
            self.assertGreater(row["roe"], 0)
            self.assertGreater(row["net_income"], 0)
            self.assertGreater(row["total_equity"], 0)
            self.assertGreater(row["revenue"], row["net_income"])

    def test_financials_cover_multiple_fiscal_years(self) -> None:
        rows = MockProvider().fetch_financials(MarketTarget.US, "AAPL")

        periods = {row["fiscal_period"] for row in rows}
        self.assertGreaterEqual(len(periods), 5)
        for row in rows:
            self.assertEqual(row["market"], "US")
            self.assertEqual(row["fiscal_period"], str(row["report_date"].year))

    def test_both_target_raises(self) -> None:
        with self.assertRaises(ValueError):
            MockProvider().fetch_financials(MarketTarget.BOTH, "0700.HK")
