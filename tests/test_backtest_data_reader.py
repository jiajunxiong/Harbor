"""Storage-backed BacktestDataReader tests (MVP 2 / SP 2.8)."""

import unittest
from datetime import date

from sqlalchemy.dialects import postgresql

from harbor.core.adjustments import ActionTerms
from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import (
    AdjustmentFactor,
    BacktestDataReader,
    DailyQuote,
    Dividend,
    FundamentalRecord,
)
from harbor.core.equity import EntitlementEvent
from harbor.core.market_registry import CorporateActionType
from harbor.core.stock_pool import StockPoolMembership
from harbor.storage.backtest_data_reader import (
    StorageBacktestDataReader,
    _adjustment_factor_from_row,
    _dividend_from_row,
    _entitlements_from_rows,
    _fundamental_from_row,
    _membership_from_row,
    _quote_from_row,
    _securities_rows_statement,
    _securities_statement,
)


class RowMappingTests(unittest.TestCase):
    """Verify DB rows are mapped to immutable domain records."""

    def test_quote_from_row(self) -> None:
        quote = _quote_from_row(
            {
                "market": "US",
                "symbol": "AAPL",
                "date": date(2026, 1, 2),
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1_000_000,
                "adjusted_close": 101.0,
            }
        )
        self.assertIsInstance(quote, DailyQuote)
        self.assertEqual(quote.market, Market.US)
        self.assertEqual(quote.day, date(2026, 1, 2))
        self.assertEqual(quote.close, 101.0)
        self.assertEqual(quote.volume, 1_000_000)

    def test_dividend_from_row_marks_special(self) -> None:
        special = _dividend_from_row(
            {
                "market": "HK",
                "symbol": "0005.HK",
                "ex_date": date(2026, 3, 1),
                "record_date": date(2026, 2, 27),
                "payment_date": date(2026, 3, 15),
                "amount": 2.0,
                "type": "special",
                "currency": "HKD",
            }
        )
        self.assertIsInstance(special, Dividend)
        self.assertTrue(special.is_special)
        self.assertEqual(special.currency, Currency.HKD)

        regular = _dividend_from_row(
            {
                "market": "HK",
                "symbol": "0005.HK",
                "ex_date": date(2026, 3, 1),
                "record_date": None,
                "payment_date": None,
                "amount": 1.0,
                "type": "regular",
                "currency": "HKD",
            }
        )
        self.assertFalse(regular.is_special)

    def test_fundamental_from_row_maps_disclosure_date(self) -> None:
        dated = _fundamental_from_row(
            {
                "market": "US",
                "symbol": "AAPL",
                "report_date": date(2025, 12, 31),
                "fiscal_period": "FY2025",
                "disclosure_date": date(2026, 2, 15),
                "roe": 0.3,
                "net_income": None,
                "total_equity": 10.0,
                "revenue": 20.0,
            }
        )
        self.assertIsInstance(dated, FundamentalRecord)
        self.assertEqual(dated.available_on, date(2026, 2, 15))
        self.assertEqual(dated.roe, 0.3)
        self.assertIsNone(dated.net_income)

        undated = _fundamental_from_row(
            {
                "market": "US",
                "symbol": "AAPL",
                "report_date": date(2025, 12, 31),
                "fiscal_period": "FY2025",
                "disclosure_date": None,
            }
        )
        self.assertIsNone(undated.available_on)

    def test_adjustment_factor_from_row(self) -> None:
        factor = _adjustment_factor_from_row(
            {
                "market": "US",
                "symbol": "AAPL",
                "date": date(2026, 1, 2),
                "cumulative_factor": 2.0,
                "daily_factor": 1.0,
            }
        )
        self.assertIsInstance(factor, AdjustmentFactor)
        self.assertEqual(factor.cumulative_factor, 2.0)

    def test_entitlements_from_rows_builds_terms(self) -> None:
        action_rows = [
            {
                "action_id": "a-split",
                "action_type": "split",
                "ex_date": date(2026, 1, 5),
                "record_date": date(2026, 1, 2),
            },
            {
                "action_id": "a-div",
                "action_type": "dividend",
                "ex_date": date(2026, 2, 1),
                "record_date": None,
            },
        ]
        term_rows = [
            {"action_id": "a-split", "term_type": "ratio", "value": 2.0},
            {"action_id": "a-div", "term_type": "price", "value": 1.5},
        ]
        events = _entitlements_from_rows(action_rows, term_rows)
        self.assertEqual(len(events), 2)
        self.assertIsInstance(events[0], EntitlementEvent)
        self.assertEqual(events[0].action_type, CorporateActionType.SPLIT)
        self.assertEqual(events[0].terms, ActionTerms(ratio=2.0))
        self.assertEqual(events[1].action_type, CorporateActionType.DIVIDEND)
        self.assertEqual(events[1].terms, ActionTerms(price=1.5))

    def test_entitlements_default_terms_when_missing(self) -> None:
        events = _entitlements_from_rows(
            [{"action_id": "a-1", "action_type": "merger", "ex_date": None, "record_date": None}],
            [],
        )
        self.assertEqual(events[0].terms, ActionTerms())
        self.assertIsNone(events[0].ex_date)

    def test_entitlements_reject_unknown_action_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown corporate action type"):
            _entitlements_from_rows(
                [
                    {
                        "action_id": "a-1",
                        "action_type": "buyback",
                        "ex_date": None,
                        "record_date": None,
                    }
                ],
                [],
            )


class UniverseTests(unittest.TestCase):
    """Verify the survivorship-bias-free securities query (SP 2.10)."""

    def test_securities_statement_filters_list_and_delist_dates(self) -> None:
        statement = _securities_statement(Market.HK, date(2026, 1, 2))
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM securities", sql)
        self.assertIn("securities.market = %(market_1)s", sql)
        self.assertIn("securities.list_date <= %(list_date_1)s", sql)
        self.assertIn("securities.delist_date IS NULL", sql)
        self.assertIn("securities.delist_date >= %(delist_date_1)s", sql)
        self.assertIn("ORDER BY securities.symbol", sql)


class ReaderContractTests(unittest.TestCase):
    """Verify the reader satisfies the interface and builds scoped queries."""

    def test_reader_is_a_backtest_data_reader(self) -> None:
        self.assertTrue(issubclass(StorageBacktestDataReader, BacktestDataReader))
        reader = StorageBacktestDataReader(connection=object())  # type: ignore[arg-type]
        self.assertIsNotNone(reader)

    def test_reader_exposes_fx_rate(self) -> None:
        self.assertTrue(hasattr(StorageBacktestDataReader, "fx_rate"))

    def test_fx_rate_same_currency_returns_one(self) -> None:
        reader = StorageBacktestDataReader(connection=object())  # type: ignore[arg-type]
        self.assertEqual(reader.fx_rate(Currency.HKD, Currency.HKD, date(2026, 1, 2)), 1.0)

    def test_reader_exposes_fx_rate_with_date(self) -> None:
        self.assertTrue(hasattr(StorageBacktestDataReader, "fx_rate_with_date"))

    def test_fx_rate_with_date_same_currency_returns_one_at_as_of(self) -> None:
        reader = StorageBacktestDataReader(connection=object())  # type: ignore[arg-type]
        record = reader.fx_rate_with_date(Currency.HKD, Currency.HKD, date(2026, 1, 2))
        self.assertIsNotNone(record)
        self.assertEqual(record.rate, 1.0)  # type: ignore[union-attr]
        self.assertEqual(record.date, date(2026, 1, 2))  # type: ignore[union-attr]


class StockPoolReaderTests(unittest.TestCase):
    """Verify the reader's historical stock pool integration (SP 2.10)."""

    def test_reader_exposes_stock_pool(self) -> None:
        self.assertTrue(hasattr(StorageBacktestDataReader, "stock_pool"))

    def test_membership_from_row_maps_listing_and_delisting_dates(self) -> None:
        membership = _membership_from_row(
            {
                "market": "HK",
                "symbol": "0005.HK",
                "list_date": date(1990, 1, 1),
                "delist_date": None,
            },
            "hkex_universe",
        )
        self.assertIsInstance(membership, StockPoolMembership)
        self.assertEqual(membership.market, Market.HK)
        self.assertEqual(membership.effective_date, date(1990, 1, 1))
        self.assertIsNone(membership.expiry_date)
        self.assertEqual(membership.source, "hkex_universe")

    def test_securities_rows_statement_orders_by_symbol_without_date_filters(self) -> None:
        statement = _securities_rows_statement(Market.US)
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM securities", sql)
        self.assertIn("securities.market = %(market_1)s", sql)
        self.assertIn("ORDER BY securities.symbol", sql)
        self.assertNotIn("list_date <=", sql)
        self.assertNotIn("delist_date IS NULL", sql)


if __name__ == "__main__":
    unittest.main()
