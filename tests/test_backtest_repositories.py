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
from harbor.storage.models import (
    BacktestFill,
    BacktestMetric,
    BacktestNetValue,
    BacktestPosition,
    BacktestRebalance,
    BacktestRejectedTrade,
    Base,
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


class BacktestResultRepositoryTests(unittest.TestCase):
    """Verify the backtest result tables repository (MVP 2 / SP 2.7)."""

    def setUp(self) -> None:
        self.repository = BacktestRepository(connection=object())  # type: ignore[arg-type]

    def test_result_models_are_linked_to_backtest_runs(self) -> None:
        models = (
            BacktestNetValue,
            BacktestPosition,
            BacktestFill,
            BacktestRebalance,
            BacktestMetric,
            BacktestRejectedTrade,
        )
        for model in models:
            references = {fk.column.table.name for fk in model.__table__.foreign_keys}
            self.assertIn("backtest_runs", references, msg=model.__name__)

    def test_insert_net_values_tags_run_id(self) -> None:
        statement = self.repository._insert_results_statement(
            BacktestNetValue,
            "run-001",
            [
                {
                    "as_of_date": date(2026, 8, 5),
                    "currency": "HKD",
                    "cash": 1.0,
                    "securities_value": 2.0,
                    "fees_paid": 0.0,
                    "total_value": 3.0,
                }
            ],
        )
        assert statement is not None
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertIn("INSERT INTO backtest_net_values", compiled.string)
        self.assertIn("backtest_run_id", compiled.string)
        self.assertIn("run-001", compiled.params.values())

    def test_insert_marks_run_id_for_each_result_table(self) -> None:
        rows_by_model: dict[type[Base], list[dict[str, Any]]] = {
            BacktestNetValue: [
                {
                    "as_of_date": date(2026, 8, 5),
                    "currency": "HKD",
                    "cash": 1.0,
                    "securities_value": 1.0,
                    "fees_paid": 0.0,
                    "total_value": 2.0,
                }
            ],
            BacktestPosition: [
                {
                    "market": "HK",
                    "symbol": "0005.HK",
                    "as_of_date": date(2026, 8, 5),
                    "quantity": 100.0,
                    "average_cost": 60.0,
                    "currency": "HKD",
                }
            ],
            BacktestFill: [
                {
                    "trade_date": date(2026, 8, 5),
                    "market": "HK",
                    "symbol": "0005.HK",
                    "side": "BUY",
                    "quantity": 100.0,
                    "price": 60.0,
                    "fee": 5.0,
                    "currency": "HKD",
                    "order_ref": "o-1",
                }
            ],
            BacktestRebalance: [
                {
                    "market": "HK",
                    "rebalance_date": date(2026, 8, 5),
                    "ref": "rebalance-2026Q3",
                }
            ],
            BacktestMetric: [{"metric_name": "total_return", "as_of_date": None, "value": 0.05}],
            BacktestRejectedTrade: [
                {
                    "market": "HK",
                    "symbol": "0005.HK",
                    "side": None,
                    "quantity": None,
                    "reason": "suspended",
                    "order_ref": None,
                }
            ],
        }
        for model, rows in rows_by_model.items():
            statement = self.repository._insert_results_statement(model, "run-001", rows)
            assert statement is not None
            compiled = statement.compile(dialect=postgresql.dialect())
            self.assertIn("backtest_run_id", compiled.string, msg=model.__name__)
            self.assertIn("run-001", compiled.params.values(), msg=model.__name__)

    def test_market_scoped_insert_rejects_mixed_markets(self) -> None:
        with self.assertRaisesRegex(ValueError, "must target market"):
            self.repository.insert_positions(
                "HK",
                "run-001",
                [
                    {"market": "HK", "symbol": "0005.HK", "as_of_date": date(2026, 8, 5)},
                    {"market": "US", "symbol": "AAPL", "as_of_date": date(2026, 8, 5)},
                ],
            )

    def test_list_net_values_filters_by_run_only(self) -> None:
        statement = self.repository.list_net_values("run-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM backtest_net_values", sql)
        self.assertIn("backtest_net_values.backtest_run_id = %(backtest_run_id_1)s", sql)

    def test_list_positions_filters_by_run_and_market(self) -> None:
        statement = self.repository.list_positions("HK", "run-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM backtest_positions", sql)
        self.assertIn("backtest_positions.backtest_run_id = %(backtest_run_id_1)s", sql)
        self.assertIn("backtest_positions.market = %(market_1)s", sql)

    def test_list_fills_filters_by_run_and_market(self) -> None:
        statement = self.repository.list_fills("HK", "run-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM backtest_fills", sql)
        self.assertIn("backtest_fills.market = %(market_1)s", sql)

    def test_list_rebalances_filters_by_run_and_market(self) -> None:
        statement = self.repository.list_rebalances("US", "run-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM backtest_rebalances", sql)
        self.assertIn("backtest_rebalances.market = %(market_1)s", sql)

    def test_list_metrics_filters_by_run_only(self) -> None:
        statement = self.repository.list_metrics("run-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM backtest_metrics", sql)
        self.assertIn("backtest_metrics.backtest_run_id = %(backtest_run_id_1)s", sql)
        self.assertNotIn(".market =", sql)

    def test_list_rejected_trades_filters_by_run_and_market(self) -> None:
        statement = self.repository.list_rejected_trades("HK", "run-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM backtest_rejected_trades", sql)
        self.assertIn("backtest_rejected_trades.market = %(market_1)s", sql)


if __name__ == "__main__":
    unittest.main()
