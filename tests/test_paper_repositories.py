"""Paper repository tests (MVP 4 / SP 4.5-4.8).

Verifies the ``PaperRepository`` contract without a database by compiling the
statements: the master-row insert/update/get and the idempotent artifact
inserts for orders, fills, risk approvals, circuit breakers, net values and
reconciliation differences, all scoped by ``paper_run_id``.
"""

import unittest
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.dialects import postgresql

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.paper_domain import (
    ApprovalDecision,
    CircuitBreakerKind,
    CircuitBreakerState,
    PaperFill,
    PaperOrder,
    PaperOrderStatus,
    PaperPriceType,
    PaperStatus,
    RiskApproval,
)
from harbor.storage.models import (
    CircuitBreaker,
    PaperNetValue,
    PaperReconciliationDifference,
)
from harbor.storage.models import (
    PaperFill as PaperFillModel,
)
from harbor.storage.models import (
    PaperOrder as PaperOrderModel,
)
from harbor.storage.models import (
    RiskApproval as RiskApprovalModel,
)
from harbor.storage.paper_repositories import (
    _PAPER_STATUSES,
    PaperRepository,
    _approval_row,
    _breaker_row,
    _fill_row,
    _order_row,
)

_UTC = timezone.utc


def _order(**overrides: object) -> PaperOrder:
    """Return a valid paper order with overridable fields."""
    fields: dict[str, object] = {
        "order_id": "order-1",
        "paper_run_id": "paper-1",
        "market": Market.HK,
        "symbol": "0001.HK",
        "side": OrderSide.BUY,
        "quantity": 100.0,
        "currency": Currency.HKD,
        "price_type": PaperPriceType.REFERENCE,
        "status": PaperOrderStatus.CREATED,
        "created_at": datetime(2026, 1, 2, 9, tzinfo=_UTC),
        "intention_id": "sig-1",
    }
    fields.update(overrides)
    return PaperOrder(**fields)  # type: ignore[arg-type]


def _fill(**overrides: object) -> PaperFill:
    """Return a valid paper fill with overridable fields."""
    fields: dict[str, object] = {
        "fill_id": "fill-1",
        "paper_order_id": "order-1",
        "paper_run_id": "paper-1",
        "market": Market.HK,
        "symbol": "0001.HK",
        "side": OrderSide.BUY,
        "quantity": 100.0,
        "price": 50.0,
        "currency": Currency.HKD,
        "trade_date": date(2026, 1, 2),
        "fee": 5.0,
    }
    fields.update(overrides)
    return PaperFill(**fields)  # type: ignore[arg-type]


def _approval(**overrides: object) -> RiskApproval:
    """Return a valid risk approval with overridable fields."""
    fields: dict[str, object] = {
        "approval_id": "ap-1",
        "paper_run_id": "paper-1",
        "scope": "run:paper-1",
        "approver": "alice",
        "decision": ApprovalDecision.APPROVED,
        "rule": "drawdown_circuit",
        "reason": "reviewed and accepted",
        "decided_at": datetime(2026, 1, 3, 10, tzinfo=_UTC),
    }
    fields.update(overrides)
    return RiskApproval(**fields)  # type: ignore[arg-type]


def _breaker(**overrides: object) -> CircuitBreakerState:
    """Return a valid circuit-breaker state with overridable fields."""
    fields: dict[str, object] = {
        "breaker_id": "cb-1",
        "paper_run_id": "paper-1",
        "kind": CircuitBreakerKind.DRAWDOWN,
        "triggered": True,
        "scope": "new_orders",
        "reason": "drawdown 10%",
        "frozen_at": datetime(2026, 2, 1, 9, tzinfo=_UTC),
    }
    fields.update(overrides)
    return CircuitBreakerState(**fields)  # type: ignore[arg-type]


class PaperRepositoryTests(unittest.TestCase):
    """Verify the paper runs master-table repository (SP 4.5 / 4.10)."""

    def setUp(self) -> None:
        self.repository = PaperRepository(connection=object())  # type: ignore[arg-type]
        self.arguments: dict[str, Any] = {
            "run_id": "paper-001",
            "strategy": "mvp3-qualified",
            "strategy_version": "1.0.0",
            "config_hash": "b" * 64,
            "config_snapshot": {
                "strategy": "mvp3-qualified",
                "strategy_version": "1.0.0",
                "base_currency": "HKD",
            },
            "dataset_fingerprint": "d" * 64,
            "code_version": "1.0.0",
            "markets": ["HK", "US"],
            "base_currency": "HKD",
            "created_at": datetime(2026, 8, 30, 1, 0, tzinfo=_UTC),
            "status": PaperStatus.DRAFT.value,
        }

    def test_status_vocabulary_matches_domain_enum(self) -> None:
        self.assertEqual(_PAPER_STATUSES, {status.value for status in PaperStatus})

    def test_create_run_conflicts_on_run_id(self) -> None:
        statement = self.repository._create_run_statement(**self.arguments)
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("INSERT INTO paper_runs", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("(run_id)", sql)

    def test_create_run_captures_replay_identity(self) -> None:
        statement = self.repository._create_run_statement(**self.arguments)
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertEqual(compiled.params["config_hash"], self.arguments["config_hash"])
        self.assertEqual(
            compiled.params["dataset_fingerprint"], self.arguments["dataset_fingerprint"]
        )
        self.assertEqual(compiled.params["markets"], ["HK", "US"])
        self.assertIn("config_snapshot", compiled.string)

    def test_create_run_defaults_to_draft(self) -> None:
        statement = self.repository._create_run_statement(**self.arguments)
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertEqual(compiled.params["status"], "DRAFT")

    def test_create_run_rejects_unknown_status(self) -> None:
        arguments = dict(self.arguments)
        arguments.pop("status")
        with self.assertRaisesRegex(ValueError, "Unknown paper status"):
            self.repository._create_run_statement(**arguments, status="PENDING")

    def test_update_run_sets_status_and_timestamps(self) -> None:
        statement = self.repository._update_run_statement(
            run_id="paper-001",
            status=PaperStatus.ACTIVE.value,
            started_at=datetime(2026, 8, 30, 2, 0, tzinfo=_UTC),
            stopped_at=None,
        )
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertIn("UPDATE paper_runs", compiled.string)
        self.assertIn("paper_runs.run_id = %(run_id_1)s", compiled.string)
        self.assertEqual(compiled.params["status"], "ACTIVE")

    def test_update_run_rejects_unknown_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown paper status"):
            self.repository._update_run_statement(
                run_id="paper-001",
                status="PENDING",
                started_at=None,
                stopped_at=None,
            )

    def test_get_run_filters_by_run_id(self) -> None:
        statement = self.repository.get_run("paper-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM paper_runs", sql)
        self.assertIn("paper_runs.run_id = %(run_id_1)s", sql)


class PaperArtifactRepositoryTests(unittest.TestCase):
    """Verify the paper artifact tables repository (SP 4.6-4.8)."""

    def setUp(self) -> None:
        self.repository = PaperRepository(connection=object())  # type: ignore[arg-type]

    def test_insert_orders_conflicts_on_run_and_order(self) -> None:
        statement = PaperRepository._insert_rows_statement(
            PaperOrderModel,
            "paper-1",
            [_order_row(_order())],
            ("paper_run_id", "order_id"),
        )
        assert statement is not None
        compiled = statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
        sql = compiled.string
        self.assertIn("INSERT INTO paper_orders", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("(paper_run_id, order_id)", sql)
        self.assertIn("order_id", sql)
        self.assertIn("CREATED", sql)

    def test_insert_fills_conflicts_on_run_and_fill(self) -> None:
        statement = PaperRepository._insert_rows_statement(
            PaperFillModel,
            "paper-1",
            [_fill_row(_fill())],
            ("paper_run_id", "fill_id"),
        )
        assert statement is not None
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertIn("INSERT INTO paper_fills", compiled.string)
        self.assertIn("(paper_run_id, fill_id)", compiled.string)
        self.assertIn("order_id", compiled.string)

    def test_insert_approvals_conflicts_on_run_and_approval(self) -> None:
        statement = PaperRepository._insert_rows_statement(
            RiskApprovalModel,
            "paper-1",
            [_approval_row(_approval())],
            ("paper_run_id", "approval_id"),
        )
        assert statement is not None
        compiled = statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
        self.assertIn("INSERT INTO risk_approvals", compiled.string)
        self.assertIn("(paper_run_id, approval_id)", compiled.string)
        self.assertIn("alice", compiled.string)

    def test_insert_circuit_breakers_conflicts_on_run_and_breaker(self) -> None:
        statement = PaperRepository._insert_rows_statement(
            CircuitBreaker,
            "paper-1",
            [_breaker_row(_breaker())],
            ("paper_run_id", "breaker_id"),
        )
        assert statement is not None
        compiled = statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
        self.assertIn("INSERT INTO circuit_breakers", compiled.string)
        self.assertIn("(paper_run_id, breaker_id)", compiled.string)
        self.assertIn("DRAWDOWN", compiled.string)

    def test_insert_net_values_conflicts_on_day_and_currency(self) -> None:
        rows = [
            {
                "as_of_date": date(2026, 1, 2),
                "currency": "HKD",
                "cash": 994_995.0,
                "securities_value": 5_000.0,
                "fees_paid": 5.0,
                "total_value": 999_995.0,
            }
        ]
        statement = PaperRepository._insert_rows_statement(
            PaperNetValue,
            "paper-1",
            rows,
            ("paper_run_id", "as_of_date", "currency"),
        )
        assert statement is not None
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertIn("INSERT INTO paper_net_values", compiled.string)
        self.assertIn("(paper_run_id, as_of_date, currency)", compiled.string)
        self.assertIn("cash", compiled.string)

    def test_insert_reconciliation_differences_conflicts_on_check(self) -> None:
        rows = [
            {
                "as_of_date": date(2026, 1, 2),
                "check_name": "assets_close",
                "expected": 999_995.0,
                "actual": 999_990.0,
                "detail": "gap 5.0",
            }
        ]
        statement = PaperRepository._insert_rows_statement(
            PaperReconciliationDifference,
            "paper-1",
            rows,
            ("paper_run_id", "as_of_date", "check_name"),
        )
        assert statement is not None
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertIn("INSERT INTO paper_reconciliation_differences", compiled.string)
        self.assertIn("(paper_run_id, as_of_date, check_name)", compiled.string)
        self.assertIn("check_name", compiled.string)

    def test_empty_rows_produce_no_statement(self) -> None:
        statement = PaperRepository._insert_rows_statement(
            PaperOrderModel, "paper-1", [], ("paper_run_id", "order_id")
        )
        self.assertIsNone(statement)

    def test_list_orders_filters_by_run_id(self) -> None:
        statement = self.repository.list_orders("paper-1")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM paper_orders", sql)
        self.assertIn("paper_orders.paper_run_id = %(paper_run_id_1)s", sql)

    def test_list_fills_filters_by_run_id(self) -> None:
        statement = self.repository.list_fills("paper-1")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM paper_fills", sql)
        self.assertIn("paper_fills.paper_run_id = %(paper_run_id_1)s", sql)

    def test_list_approvals_filters_by_run_id(self) -> None:
        statement = self.repository.list_approvals("paper-1")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM risk_approvals", sql)
        self.assertIn("risk_approvals.paper_run_id = %(paper_run_id_1)s", sql)

    def test_list_circuit_breakers_filters_by_run_id(self) -> None:
        statement = self.repository.list_circuit_breakers("paper-1")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM circuit_breakers", sql)
        self.assertIn("circuit_breakers.paper_run_id = %(paper_run_id_1)s", sql)

    def test_list_net_values_filters_by_run_id(self) -> None:
        statement = self.repository.list_net_values("paper-1")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM paper_net_values", sql)
        self.assertIn("paper_net_values.paper_run_id = %(paper_run_id_1)s", sql)

    def test_list_reconciliation_differences_filters_by_run_id(self) -> None:
        statement = self.repository.list_reconciliation_differences("paper-1")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM paper_reconciliation_differences", sql)
        self.assertIn(
            "paper_reconciliation_differences.paper_run_id = %(paper_run_id_1)s",
            sql,
        )
