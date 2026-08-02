"""Database model contract tests."""

import unittest

from harbor.storage.models import Security


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
