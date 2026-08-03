"""Repository layer tests."""

import inspect
import unittest
from datetime import date, datetime, timezone

from sqlalchemy.dialects import postgresql

from harbor.storage.models import DailyQuote, IngestionRun
from harbor.storage.repositories import Repository


class RepositoryTests(unittest.TestCase):
    """Verify the repository layer contract."""

    def test_every_public_method_accepts_market(self) -> None:
        methods = [
            member
            for name, member in inspect.getmembers(Repository, inspect.isfunction)
            if not name.startswith("_")
        ]
        self.assertTrue(methods)
        for method in methods:
            parameters = inspect.signature(method).parameters
            self.assertIn("market", parameters, msg=method.__name__)

    def test_upsert_daily_quotes_conflicts_on_primary_key(self) -> None:
        repository = Repository(connection=object())  # type: ignore[arg-type]
        statement = repository._upsert_statement(
            DailyQuote,
            [{"market": "HK", "symbol": "0005.HK", "date": date(2026, 8, 1)}],
        )
        self.assertIsNotNone(statement)
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("(market, symbol, date)", sql)

    def test_upsert_rejects_mixed_market_rows(self) -> None:
        repository = Repository(connection=object())  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            repository.upsert_daily_quotes(
                "HK",
                [
                    {"market": "HK", "symbol": "0005.HK", "date": date(2026, 8, 1)},
                    {"market": "US", "symbol": "AAPL", "date": date(2026, 8, 1)},
                ],
            )

    def test_create_ingestion_run_is_idempotent_on_run_id(self) -> None:
        repository = Repository(connection=object())  # type: ignore[arg-type]
        statement = repository._upsert_statement(
            IngestionRun,
            [
                {
                    "run_id": "run-1",
                    "start_time": datetime(2026, 8, 3, tzinfo=timezone.utc),
                    "status": "running",
                    "market": "HK",
                    "source": "mock",
                    "records_processed": 0,
                }
            ],
        )
        self.assertIsNotNone(statement)
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("(run_id)", sql)

    def test_list_daily_quotes_filters_by_market(self) -> None:
        repository = Repository(connection=object())  # type: ignore[arg-type]
        statement = repository.list_daily_quotes("HK")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM daily_quotes", sql)
        self.assertIn("daily_quotes.market = %(market_1)s", sql)
