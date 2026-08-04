"""Live yfinance contract tests (SP 1.103 HK, 1.104 US).

Make small real calls to yfinance for Hong Kong and United States symbols and
verify that the standardized data structures match the expected contract
(field set and value types). The tests are skipped when yfinance is not
installed or the live network call fails.
"""

import importlib
import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.infrastructure.data_providers.yfinance import (
    HKYFinanceProvider,
    USYFinanceProvider,
)

_QUOTE_START = date(2026, 1, 5)
_QUOTE_END = date(2026, 1, 9)
_DIVIDEND_START = date(2024, 1, 1)

_DAILY_KEYS = {
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
}
_DIVIDEND_KEYS = {
    "market",
    "symbol",
    "ex_date",
    "record_date",
    "payment_date",
    "amount",
    "type",
    "currency",
}


def _yfinance_available() -> bool:
    """Return whether the yfinance package can be imported."""
    try:
        importlib.import_module("yfinance")
    except ImportError:
        return False
    return True


@unittest.skipUnless(_yfinance_available(), "yfinance is not installed")
class YFinanceContractTests(unittest.TestCase):
    """Live yfinance contract tests for both markets."""

    def _daily_quotes(self, provider: object, market: MarketTarget, symbol: str) -> list[dict]:
        try:
            return list(provider.fetch_daily_quotes(market, symbol, _QUOTE_START, _QUOTE_END))
        except Exception as error:  # pragma: no cover - network dependent
            self.skipTest(f"Live yfinance call failed: {error}")

    def _dividends(self, provider: object, market: MarketTarget, symbol: str) -> list[dict]:
        try:
            return list(provider.fetch_dividends(market, symbol, _DIVIDEND_START, date.today()))
        except Exception as error:  # pragma: no cover - network dependent
            self.skipTest(f"Live yfinance call failed: {error}")

    def _assert_daily_quote_rows(
        self,
        rows: list[dict],
        market: MarketTarget,
        symbol: str,
    ) -> None:
        self.assertGreater(len(rows), 0)
        for row in rows:
            self.assertEqual(set(row.keys()), _DAILY_KEYS)
            self.assertEqual(row["market"], market.value)
            self.assertEqual(row["symbol"], symbol)
            self.assertEqual(row["source"], "yfinance")
            self.assertIsInstance(row["date"], date)
            for field in ("open", "high", "low", "close", "adjusted_close"):
                self.assertIsInstance(row[field], float)
                self.assertGreater(row[field], 0)
            self.assertIsInstance(row["volume"], int)
            self.assertGreaterEqual(row["volume"], 0)

    def _assert_dividend_rows(self, rows: list[dict], market: MarketTarget) -> None:
        if not rows:
            self.skipTest("No dividend data returned for the symbol.")
        for row in rows:
            self.assertEqual(set(row.keys()), _DIVIDEND_KEYS)
            self.assertEqual(row["market"], market.value)
            self.assertIsInstance(row["ex_date"], date)
            self.assertIsInstance(row["amount"], float)
            self.assertGreater(row["amount"], 0)
            self.assertEqual(row["type"], "regular")
            expected_currency = "HKD" if market is MarketTarget.HK else "USD"
            self.assertEqual(row["currency"], expected_currency)

    def test_hk_daily_quotes_contract(self) -> None:
        """SP 1.103: Hong Kong daily quote structure from a real call."""
        rows = self._daily_quotes(HKYFinanceProvider(), MarketTarget.HK, "0700.HK")
        self._assert_daily_quote_rows(rows, MarketTarget.HK, "0700.HK")

    def test_us_daily_quotes_contract(self) -> None:
        """SP 1.104: United States daily quote structure from a real call."""
        rows = self._daily_quotes(USYFinanceProvider(), MarketTarget.US, "AAPL")
        self._assert_daily_quote_rows(rows, MarketTarget.US, "AAPL")

    def test_hk_dividends_contract(self) -> None:
        """SP 1.103: Hong Kong dividend structure from a real call."""
        rows = self._dividends(HKYFinanceProvider(), MarketTarget.HK, "0700.HK")
        self._assert_dividend_rows(rows, MarketTarget.HK)

    def test_us_dividends_contract(self) -> None:
        """SP 1.104: United States dividend structure from a real call."""
        rows = self._dividends(USYFinanceProvider(), MarketTarget.US, "AAPL")
        self._assert_dividend_rows(rows, MarketTarget.US)
