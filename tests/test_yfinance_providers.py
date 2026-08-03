"""yfinance provider tests."""

import unittest
from datetime import date, datetime, timezone

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability
from harbor.infrastructure.data_providers.yfinance import (
    HKYFinanceProvider,
    USYFinanceProvider,
    standardize_daily_quotes,
    standardize_dividends,
    standardize_financials,
    standardize_splits,
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


class DividendSplitStandardizationTests(unittest.TestCase):
    """Verify the yfinance dividend and split standardization contract."""

    def test_standardize_dividends_maps_to_dividend_rows(self) -> None:
        dividends = {
            date(2026, 3, 2): 3.85,
            date(2026, 9, 1): 3.85,
        }

        rows = standardize_dividends(MarketTarget.HK, "0700.HK", dividends, "yfinance")

        self.assertEqual(len(rows), 2)
        row = rows[0]
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
        self.assertEqual(row["ex_date"], date(2026, 3, 2))
        self.assertIsNone(row["record_date"])
        self.assertIsNone(row["payment_date"])
        self.assertEqual(row["amount"], 3.85)
        self.assertEqual(row["type"], "regular")
        self.assertEqual(row["currency"], "HKD")

    def test_standardize_dividends_uses_market_currency(self) -> None:
        rows = standardize_dividends(MarketTarget.US, "AAPL", {date(2026, 2, 9): 0.25}, "yfinance")

        self.assertEqual(rows[0]["currency"], "USD")

    def test_standardize_dividends_drops_invalid_rows(self) -> None:
        dividends = {
            date(2026, 3, 2): 3.85,
            date(2026, 9, 1): None,
        }

        rows = standardize_dividends(MarketTarget.HK, "0700.HK", dividends, "yfinance")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ex_date"], date(2026, 3, 2))

    def test_standardize_splits_maps_to_corporate_action_rows(self) -> None:
        rows = standardize_splits(MarketTarget.US, "AAPL", {date(2026, 6, 1): 4.0}, "yfinance")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            set(row),
            {
                "market",
                "symbol",
                "action_id",
                "announce_date",
                "ex_date",
                "record_date",
                "effective_date",
                "action_type",
                "status",
                "source",
            },
        )
        self.assertEqual(row["market"], "US")
        self.assertEqual(row["symbol"], "AAPL")
        self.assertEqual(row["ex_date"], date(2026, 6, 1))
        self.assertEqual(row["action_type"], "split")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["source"], "yfinance")
        self.assertTrue(row["action_id"].startswith("AAPL-split-"))

    def test_standardize_splits_assigns_unique_action_ids(self) -> None:
        splits = {date(2026, 1, 5): 2.0, date(2026, 7, 1): 3.0}

        rows = standardize_splits(MarketTarget.HK, "0005.HK", splits, "yfinance")

        action_ids = {row["action_id"] for row in rows}
        self.assertEqual(len(action_ids), len(rows))
        self.assertEqual(rows[0]["action_type"], "split")


class FinancialStandardizationTests(unittest.TestCase):
    """Verify the yfinance financials standardization contract."""

    def test_standardize_financials_extracts_metrics(self) -> None:
        info = {
            "returnOnEquity": 0.25,
            "netIncomeToCommon": 100_000_000_000.0,
            "totalStockholderEquity": 400_000_000_000.0,
            "totalRevenue": 390_000_000_000.0,
        }

        rows = standardize_financials(MarketTarget.US, "AAPL", info, date(2026, 3, 31))

        self.assertEqual(len(rows), 1)
        row = rows[0]
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
        self.assertEqual(row["market"], "US")
        self.assertEqual(row["symbol"], "AAPL")
        self.assertEqual(row["report_date"], date(2026, 3, 31))
        self.assertEqual(row["fiscal_period"], "2026")
        self.assertEqual(row["roe"], 0.25)
        self.assertEqual(row["net_income"], 100_000_000_000.0)
        self.assertEqual(row["total_equity"], 400_000_000_000.0)
        self.assertEqual(row["revenue"], 390_000_000_000.0)

    def test_standardize_financials_returns_empty_when_all_metrics_missing(self) -> None:
        rows = standardize_financials(MarketTarget.HK, "0700.HK", {}, date(2026, 3, 31))

        self.assertEqual(rows, [])

    def test_standardize_financials_keeps_missing_metrics_as_none(self) -> None:
        info = {"returnOnEquity": 0.2}

        rows = standardize_financials(MarketTarget.HK, "0700.HK", info, date(2026, 3, 31))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["market"], "HK")
        self.assertEqual(rows[0]["roe"], 0.2)
        self.assertIsNone(rows[0]["net_income"])
        self.assertIsNone(rows[0]["total_equity"])
        self.assertIsNone(rows[0]["revenue"])
