"""AkShare provider tests."""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability
from harbor.infrastructure.data_providers.akshare import (
    HKAKShareProvider,
    standardize_ak_corporate_actions,
    standardize_ak_daily_quotes,
    standardize_ak_dividends,
)


class HKAKShareProviderTests(unittest.TestCase):
    """Verify the AkShare Hong Kong provider contract."""

    def test_normalizes_hk_symbol_to_five_digits(self) -> None:
        provider = HKAKShareProvider()

        self.assertEqual(provider._normalize_symbol("0700"), "00700")
        self.assertEqual(provider._normalize_symbol("0700.HK"), "00700")
        self.assertEqual(provider._normalize_symbol("00005.HK"), "00005")

    def test_serves_hk_market_only(self) -> None:
        capabilities = HKAKShareProvider().capabilities()

        self.assertTrue(capabilities.supports(MarketTarget.HK, Capability.DAILY_QUOTES))
        self.assertFalse(capabilities.supports(MarketTarget.US, Capability.DAILY_QUOTES))


class AkDailyQuoteStandardizationTests(unittest.TestCase):
    """Verify the AkShare Hong Kong daily standardization contract."""

    def test_maps_chinese_columns_to_daily_quote_rows(self) -> None:
        dates = [date(2026, 1, 5), date(2026, 1, 6)]
        columns = {
            "开盘": [100.0, 101.0],
            "最高": [105.0, 106.0],
            "最低": [99.0, 100.0],
            "收盘": [104.0, 105.0],
            "成交量": [1_000_000, 1_100_000],
        }

        rows = standardize_ak_daily_quotes(MarketTarget.HK, "0700.HK", dates, columns, "akshare")

        self.assertEqual(len(rows), 2)
        row = rows[0]
        self.assertEqual(row["market"], "HK")
        self.assertEqual(row["symbol"], "0700.HK")
        self.assertEqual(row["date"], date(2026, 1, 5))
        self.assertEqual(row["open"], 100.0)
        self.assertEqual(row["high"], 105.0)
        self.assertEqual(row["low"], 99.0)
        self.assertEqual(row["close"], 104.0)
        self.assertEqual(row["volume"], 1_000_000)
        self.assertEqual(row["adjusted_close"], 104.0)
        self.assertEqual(row["source"], "akshare")

    def test_drops_rows_with_missing_ohlc(self) -> None:
        dates = [date(2026, 1, 5), date(2026, 1, 6)]
        columns = {
            "开盘": [100.0, None],
            "最高": [105.0, None],
            "最低": [99.0, None],
            "收盘": [104.0, None],
            "成交量": [1_000_000, 0],
        }

        rows = standardize_ak_daily_quotes(MarketTarget.HK, "0700.HK", dates, columns, "akshare")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], date(2026, 1, 5))


class AkDividendCorporateActionStandardizationTests(unittest.TestCase):
    """Verify the AkShare dividend and corporate action standardization."""

    def test_dividends_map_to_dividend_rows(self) -> None:
        rows = standardize_ak_dividends(
            MarketTarget.HK, "0700.HK", {date(2026, 3, 2): 3.85}, "akshare"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["currency"], "HKD")
        self.assertEqual(rows[0]["type"], "regular")

    def test_corporate_actions_map_to_action_rows(self) -> None:
        rows = standardize_ak_corporate_actions(
            MarketTarget.HK, "0005.HK", {date(2026, 6, 1): 4.0}, "akshare"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action_type"], "split")
        self.assertEqual(rows[0]["source"], "akshare")
