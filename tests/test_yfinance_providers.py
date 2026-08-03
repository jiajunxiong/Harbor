"""yfinance provider tests."""

import unittest
from datetime import date, datetime, timezone

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability
from harbor.infrastructure.data_providers.yfinance import (
    HKYFinanceProvider,
    USYFinanceProvider,
    standardize_daily_quotes,
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


class DailyQuoteStandardizationTests(unittest.TestCase):
    """Verify the yfinance daily quote standardization contract."""

    def test_maps_yfinance_columns_to_daily_quote_rows(self) -> None:
        dates = [date(2026, 1, 5), date(2026, 1, 6)]
        columns = {
            "Open": [100.0, 101.0],
            "High": [105.0, 106.0],
            "Low": [99.0, 100.0],
            "Close": [104.0, 105.0],
            "Adj Close": [103.0, 104.0],
            "Volume": [1_000_000, 1_100_000],
        }

        rows = standardize_daily_quotes(MarketTarget.HK, "0700.HK", dates, columns, "yfinance")

        self.assertEqual(len(rows), 2)
        row = rows[0]
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
        self.assertEqual(row["market"], "HK")
        self.assertEqual(row["symbol"], "0700.HK")
        self.assertEqual(row["date"], date(2026, 1, 5))
        self.assertEqual(row["open"], 100.0)
        self.assertEqual(row["high"], 105.0)
        self.assertEqual(row["low"], 99.0)
        self.assertEqual(row["close"], 104.0)
        self.assertEqual(row["volume"], 1_000_000)
        self.assertEqual(row["adjusted_close"], 103.0)
        self.assertEqual(row["source"], "yfinance")

    def test_drops_rows_with_missing_ohlc(self) -> None:
        dates = [date(2026, 1, 5), date(2026, 1, 6)]
        columns = {
            "Open": [100.0, None],
            "High": [105.0, None],
            "Low": [99.0, None],
            "Close": [104.0, None],
            "Volume": [1_000_000, 0],
        }

        rows = standardize_daily_quotes(MarketTarget.HK, "0700.HK", dates, columns, "yfinance")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], date(2026, 1, 5))

    def test_adjusted_close_falls_back_to_close(self) -> None:
        dates = [date(2026, 1, 5)]
        columns = {
            "Open": [100.0],
            "High": [105.0],
            "Low": [99.0],
            "Close": [104.0],
            "Volume": [1_000_000],
        }

        rows = standardize_daily_quotes(MarketTarget.US, "AAPL", dates, columns, "yfinance")

        self.assertEqual(rows[0]["adjusted_close"], 104.0)

    def test_timezone_aware_datetime_index_is_normalized_to_date(self) -> None:
        dates = [datetime(2026, 1, 5, tzinfo=timezone.utc)]
        columns = {
            "Open": [100.0],
            "High": [105.0],
            "Low": [99.0],
            "Close": [104.0],
            "Volume": [1_000_000],
        }

        rows = standardize_daily_quotes(MarketTarget.HK, "0700.HK", dates, columns, "yfinance")

        self.assertEqual(rows[0]["date"], date(2026, 1, 5))
