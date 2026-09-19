"""Paper empty-database upgrade test (MVP 4 / SP 4.89).

Verifies that an empty database upgrades to the latest migration head and that
the paper master + artifact tables (paper runs, orders, fills, approvals,
circuit breakers, net values and reconciliation differences) all exist and
accept a full artifact round-trip through the SP 4.5-4.8 repository.
Skipped when ``HARBOR_TEST_DATABASE_URL`` is not set.
"""

import os
import unittest
from datetime import date, datetime, timezone

from db_guard import disposable_database_url, skip_reason
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

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
from harbor.storage.paper_repositories import PaperRepository

_TEST_DATABASE_URL = disposable_database_url()
_SKIP_REASON = skip_reason("the paper empty-database upgrade suite")
_PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _fresh_engine() -> Engine:
    engine = create_engine(_TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    return engine


def _upgrade_to_head(engine: Engine) -> None:
    from alembic.config import Config

    from alembic import command

    config = Config(os.path.join(_PROJECT_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(_PROJECT_ROOT, "alembic"))
    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
    try:
        command.upgrade(config, "head")
    finally:
        if original_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_url


@unittest.skipUnless(_TEST_DATABASE_URL, _SKIP_REASON)
class PaperEmptyDbUpgradeTests(unittest.TestCase):
    """Empty DB -> head upgrade with paper tables and artifact round-trip (SP 4.89)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = _fresh_engine()
        _upgrade_to_head(cls.engine)

    def test_all_paper_tables_exist_after_upgrade(self) -> None:
        expected = {
            "paper_runs",
            "paper_orders",
            "paper_fills",
            "risk_approvals",
            "circuit_breakers",
            "paper_net_values",
            "paper_reconciliation_differences",
        }
        inspector = __import__("sqlalchemy.inspection", fromlist=["inspect"]).inspect(self.engine)
        tables = set(inspector.get_table_names())
        for table in expected:
            self.assertIn(table, tables, f"missing paper table {table}")

    def test_paper_artifact_round_trip(self) -> None:
        with self.engine.begin() as connection:
            repository = PaperRepository(connection)
            now = datetime.now(timezone.utc)
            run_id = "paper-run-1"
            repository.create_run(
                run_id=run_id,
                strategy="paper-demo",
                strategy_version="1.0.0",
                config_hash="cfg-hash",
                config_snapshot={"markets": ["HK"], "base_currency": "HKD"},
                dataset_fingerprint="dataset-abc",
                code_version="1.0.0",
                markets=["HK"],
                base_currency="HKD",
                created_at=now,
                status=PaperStatus.DRAFT.value,
            )
            order = PaperOrder(
                order_id="order-1",
                paper_run_id=run_id,
                market=Market.HK,
                symbol="0001.HK",
                side=OrderSide.BUY,
                quantity=100.0,
                currency=Currency.HKD,
                price_type=PaperPriceType.REFERENCE,
                status=PaperOrderStatus.CREATED,
                created_at=now,
                intention_id="sig-1",
            )
            repository.insert_order(run_id, order)
            fill = PaperFill(
                fill_id="fill-1",
                paper_order_id="order-1",
                paper_run_id=run_id,
                market=Market.HK,
                symbol="0001.HK",
                side=OrderSide.BUY,
                quantity=100.0,
                price=50.0,
                currency=Currency.HKD,
                trade_date=date(2026, 1, 2),
                fee=5.0,
            )
            repository.insert_fill(run_id, fill)
            approval = RiskApproval(
                approval_id="ap-1",
                paper_run_id=run_id,
                scope="run",
                approver="risk-committee",
                decision=ApprovalDecision.APPROVED,
                rule="paper_start",
                decided_at=now,
            )
            repository.insert_approval(run_id, approval)
            # A non-triggered breaker is a *recovered* one and the domain insists it
            # records when it recovered, so this fixture is a breaker that is still
            # frozen — the state a row most directly represents.
            breaker = CircuitBreakerState(
                breaker_id="cb-1",
                paper_run_id=run_id,
                kind=CircuitBreakerKind.DAILY,
                triggered=True,
                scope="new_orders",
                reason="daily loss limit breached",
                frozen_at=now,
            )
            repository.insert_circuit_breaker(run_id, breaker)
            repository.insert_net_values(
                run_id,
                [
                    {
                        "as_of_date": date(2026, 1, 2),
                        "currency": "HKD",
                        "cash": 994_995.0,
                        "securities_value": 5_000.0,
                        "fees_paid": 5.0,
                        "total_value": 999_995.0,
                    }
                ],
            )
            repository.insert_reconciliation_differences(
                run_id,
                [
                    {
                        "as_of_date": date(2026, 1, 2),
                        "check_name": "assets_close",
                        "expected": 999_995.0,
                        "actual": 1_000_000.0,
                        "detail": "demo difference",
                    }
                ],
            )
            run_rows = [
                dict(row) for row in connection.execute(repository.get_run(run_id)).mappings()
            ]
            self.assertEqual(len(run_rows), 1)
            self.assertEqual(run_rows[0]["status"], PaperStatus.DRAFT.value)
            self.assertEqual(
                len(list(connection.execute(repository.list_orders(run_id)).mappings())),
                1,
            )
            self.assertEqual(
                len(list(connection.execute(repository.list_fills(run_id)).mappings())),
                1,
            )
            self.assertEqual(
                len(list(connection.execute(repository.list_approvals(run_id)).mappings())),
                1,
            )
            self.assertEqual(
                len(list(connection.execute(repository.list_circuit_breakers(run_id)).mappings())),
                1,
            )
            self.assertEqual(
                len(list(connection.execute(repository.list_net_values(run_id)).mappings())),
                1,
            )
            self.assertEqual(
                len(
                    list(
                        connection.execute(
                            repository.list_reconciliation_differences(run_id)
                        ).mappings()
                    )
                ),
                1,
            )
