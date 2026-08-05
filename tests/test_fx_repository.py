"""FX repository tests (MVP 2 / SP 2.12)."""

import unittest
from datetime import date

from sqlalchemy.dialects import postgresql

from harbor.storage.fx_repository import FxRepository


class FxRepositoryTests(unittest.TestCase):
    """Verify the FX rate repository contract."""

    def setUp(self) -> None:
        self.repository = FxRepository(connection=object())  # type: ignore[arg-type]

    def test_upsert_conflicts_on_pair_and_date(self) -> None:
        statement = self.repository._upsert_statement(
            [
                {
                    "from_currency": "HKD",
                    "to_currency": "USD",
                    "date": date(2026, 8, 5),
                    "rate": 0.128,
                    "source": "mock",
                    "quality": "official",
                }
            ]
        )
        assert statement is not None
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("INSERT INTO fx_rates", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("(from_currency, to_currency, date)", sql)

    def test_upsert_with_no_rows_returns_none(self) -> None:
        self.assertIsNone(self.repository._upsert_statement([]))

    def test_list_fx_rates_filters_by_pair_and_date(self) -> None:
        statement = self.repository.list_fx_rates(
            "HKD", "USD", start=date(2026, 1, 1), end=date(2026, 1, 31)
        )
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM fx_rates", sql)
        self.assertIn("fx_rates.from_currency = %(from_currency_1)s", sql)
        self.assertIn("fx_rates.to_currency = %(to_currency_1)s", sql)
        self.assertIn("fx_rates.date >= %(date_1)s", sql)
        self.assertIn("fx_rates.date <= %(date_2)s", sql)
        self.assertIn("ORDER BY fx_rates.date", sql)


if __name__ == "__main__":
    unittest.main()
