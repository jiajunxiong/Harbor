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
from harbor.storage.backtest_data_reader import (
    StorageBacktestDataReader,
    _adjustment_factor_from_row,
    _available_as_of,
    _dividend_from_row,
    _entitlements_from_rows,
    _fundamental_from_row,
    _quote_from_row,
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

    def test_fundamental_from_row_has_unknown_availability(self) -> None:
        record = _fundamental_from_row(
            {
                "market": "US",
                "symbol": "AAPL",
                "report_date": date(2025, 12, 31),
                "fiscal_period": "FY2025",
                "roe": 0.3,
                "net_income": None,
                "total_equity": 10.0,
                "revenue": 20.0,
            }
        )
        self.assertIsInstance(record, FundamentalRecord)
        self.assertIsNone(record.available_on)
        self.assertEqual(record.roe, 0.3)
        self.assertIsNone(record.net_income)

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


class PointInTimeTests(unittest.TestCase):
    """Verify point-in-time availability filtering (SP 2.9)."""

    def test_available_as_of_keeps_only_known_and_timely_records(self) -> None:
        def record(available_on: date | None) -> FundamentalRecord:
            return FundamentalRecord(
                market=Market.US,
                symbol="AAPL",
                report_date=date(2025, 12, 31),
                fiscal_period="FY2025",
                available_on=available_on,
                roe=0.3,
            )

        as_of = date(2026, 3, 31)
        kept = _available_as_of(
            [record(date(2026, 3, 1)), record(date(2026, 4, 1)), record(None)],
            as_of,
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].available_on, date(2026, 3, 1))


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


if __name__ == "__main__":
    unittest.main()
