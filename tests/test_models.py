"""Database model contract tests."""

import unittest

from harbor.storage.models import DailyQuote, Security


class SecuritiesModelTests(unittest.TestCase):
    """Verify the securities master-data schema contract."""

    def test_securities_has_required_fields_and_composite_primary_key(self) -> None:
        table = Security.__table__

        self.assertEqual(table.name, "securities")
        self.assertEqual(
            tuple(table.primary_key.columns.keys()),
            ("market", "symbol"),
        )
        self.assertEqual(
            set(table.columns.keys()),
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
        self.assertFalse(table.columns["market"].nullable)
        self.assertFalse(table.columns["symbol"].nullable)
        self.assertFalse(table.columns["is_active"].nullable)


class DailyQuoteModelTests(unittest.TestCase):
    """Verify the daily quotes schema contract."""

    def test_daily_quotes_has_required_fields_and_composite_primary_key(self) -> None:
        table = DailyQuote.__table__

        self.assertEqual(table.name, "daily_quotes")
        self.assertEqual(
            tuple(table.primary_key.columns.keys()),
            ("market", "symbol", "date"),
        )
        self.assertEqual(
            set(table.columns.keys()),
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
        self.assertEqual(
            {foreign_key.target_fullname for foreign_key in table.foreign_keys},
            {"securities.market", "securities.symbol"},
        )
