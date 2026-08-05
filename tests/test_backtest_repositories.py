"""Backtest run repository tests (MVP 2 / SP 2.6)."""

import unittest
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.dialects import postgresql

from harbor.core.backtest_domain import BacktestStatus
from harbor.storage.backtest_repositories import (
    _BACKTEST_STATUSES,
    BacktestRepository,
)


class BacktestRepositoryTests(unittest.TestCase):
    """Verify the backtest runs repository contract."""

    def setUp(self) -> None:
        self.repository = BacktestRepository(connection=object())  # type: ignore[arg-type]
        self.arguments: dict[str, Any] = {
            "run_id": "run-001",
            "config_hash": "a" * 64,
            "config_snapshot": {
                "strategy": "shareholder-return",
                "strategy_version": "1.0.0",
                "base_currency": "HKD",
            },
            "strategy": "shareholder-return",
            "strategy_version": "1.0.0",
            "code_version": "0.1.0",
            "data_cutoff": date(2026, 8, 5),
            "started_at": datetime(2026, 8, 5, 1, 0, tzinfo=timezone.utc),
            "status": BacktestStatus.RUNNING.value,
        }

    def test_status_vocabulary_matches_domain_enum(self) -> None:
        self.assertEqual(_BACKTEST_STATUSES, {status.value for status in BacktestStatus})

    def test_create_run_conflicts_on_run_id(self) -> None:
        statement = self.repository._create_statement(**self.arguments)
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("INSERT INTO backtest_runs", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("(run_id)", sql)

    def test_create_run_captures_config_hash_and_snapshot(self) -> None:
        statement = self.repository._create_statement(**self.arguments)
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertEqual(compiled.params["config_hash"], self.arguments["config_hash"])
        self.assertIn("config_snapshot", compiled.string)

    def test_create_run_defaults_to_running(self) -> None:
        statement = self.repository._create_statement(**self.arguments)
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertEqual(compiled.params["status"], "RUNNING")

    def test_create_run_rejects_unknown_status(self) -> None:
        arguments = dict(self.arguments)
        arguments.pop("status")
        with self.assertRaisesRegex(ValueError, "Unknown backtest status"):
            self.repository._create_statement(**arguments, status="PENDING")

    def test_update_run_sets_status_and_diagnostics(self) -> None:
        statement = self.repository._update_statement(
            run_id="run-001",
            status=BacktestStatus.COMPLETED.value,
            finished_at=datetime(2026, 8, 5, 2, 0, tzinfo=timezone.utc),
            error_summary=None,
        )
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertIn("UPDATE backtest_runs", compiled.string)
        self.assertIn("backtest_runs.run_id = %(run_id_1)s", compiled.string)
        self.assertEqual(compiled.params["status"], "COMPLETED")

    def test_update_run_rejects_unknown_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown backtest status"):
            self.repository._update_statement(
                run_id="run-001",
                status="PENDING",
                finished_at=None,
                error_summary=None,
            )

    def test_get_run_filters_by_run_id(self) -> None:
        statement = self.repository.get_run("run-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM backtest_runs", sql)
        self.assertIn("backtest_runs.run_id = %(run_id_1)s", sql)


if __name__ == "__main__":
    unittest.main()
