"""Paper CLI service tests (MVP 4 / SP 4.84-4.87).

Covers the paper lifecycle commands (init/start/stop/status), the signal ->
order pipeline, order list/show, approvals and the reconcile/report commands
against an in-memory store, plus the CLI wiring with patched database and
service functions.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from harbor.cli import main
from harbor.core.paper_domain import PaperStatus
from harbor.services.paper import (
    PaperServiceError,
    paper_approve_order_command,
    paper_init_command,
    paper_order_list_command,
    paper_order_show_command,
    paper_reconcile_command,
    paper_report_command,
    paper_signal_command,
    paper_start_command,
    paper_status_command,
    paper_stop_command,
)

_PAPER_YAML = """\
strategy: paper-demo
strategy_version: "1.0.0"
description: "research only — no investment advice"
markets:
  - HK
base_currency: HKD
currencies:
  - HKD
initial_capital: 1000000
rebalance_frequency: QUARTERLY
run_mode: MANUAL
risk:
  max_position_pct: 0.005
  max_single_stock_pct: 0.05
  max_industry_pct: 0.20
stop:
  max_days: 365
"""

_TRADE = date(2026, 1, 2)


class FakeStore:
    """An in-memory :class:`PaperStore` implementation."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, object]] = {}
        self.orders: list[dict[str, object]] = []
        self.fills: list[dict[str, object]] = []
        self.approvals: list[dict[str, object]] = []
        self.net_values: list[dict[str, object]] = []
        self.differences: list[dict[str, object]] = []

    def create_run(self, **kwargs: object) -> int:
        self.runs[str(kwargs["run_id"])] = dict(kwargs)
        return 1

    def get_run(self, run_id: str) -> dict[str, object] | None:
        return self.runs.get(run_id)

    def update_run(self, *, run_id: str, status: str, started_at=None, stopped_at=None) -> int:
        if run_id not in self.runs:
            return 0
        self.runs[run_id]["status"] = status
        if started_at is not None:
            self.runs[run_id]["started_at"] = started_at
        if stopped_at is not None:
            self.runs[run_id]["stopped_at"] = stopped_at
        return 1

    def insert_approval(self, paper_run_id: str, approval: object) -> int:
        self.approvals.append({"paper_run_id": paper_run_id, "approval": approval})
        return 1

    def list_approvals(self, paper_run_id: str) -> list[dict[str, object]]:
        return [
            {
                "approval_id": "ap-1",
                "scope": str(a["approval"].scope),
                "approver": str(a["approval"].approver),
                "decision": str(a["approval"].decision.value),
                "rule": str(a["approval"].rule),
                "decided_at": a["approval"].decided_at,
                "reason": a["approval"].reason,
            }
            for a in self.approvals
            if a["paper_run_id"] == paper_run_id
        ]

    def insert_orders(self, paper_run_id: str, orders: object) -> int:
        for order in orders:  # type: ignore[union-attr]
            self.orders.append(
                {
                    "order_id": order.order_id,
                    "paper_run_id": order.paper_run_id,
                    "market": order.market.value,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "quantity": order.quantity,
                    "currency": order.currency.value,
                    "price_type": order.price_type.value,
                    "status": order.status.value,
                    "created_at": order.created_at,
                    "intention_id": order.intention_id,
                    "ref": order.ref or None,
                }
            )
        return len(self.orders)

    def list_orders(self, paper_run_id: str) -> list[dict[str, object]]:
        return [row for row in self.orders if row["paper_run_id"] == paper_run_id]

    def insert_fills(self, paper_run_id: str, fills: object) -> int:
        self.fills.extend(fills)  # type: ignore[arg-type]
        return len(self.fills)

    def list_fills(self, paper_run_id: str) -> list[dict[str, object]]:
        return self.fills

    def insert_net_values(self, paper_run_id: str, rows: object) -> int:
        self.net_values.extend(rows)  # type: ignore[arg-type]
        return len(self.net_values)

    def list_net_values(self, paper_run_id: str) -> list[dict[str, object]]:
        return self.net_values

    def insert_reconciliation_differences(self, paper_run_id: str, rows: object) -> int:
        self.differences.extend(rows)  # type: ignore[arg-type]
        return len(self.differences)

    def list_reconciliation_differences(self, paper_run_id: str) -> list[dict[str, object]]:
        return self.differences


def _store_with_run(fake: FakeStore | None = None) -> tuple[FakeStore, str]:
    store = fake or FakeStore()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "paper.yaml"
        path.write_text(_PAPER_YAML, encoding="utf-8")
        result = paper_init_command(
            config_path=str(path),
            store=store,  # type: ignore[arg-type]
            code_version="1.0.0",
            dataset_fingerprint="dataset-abc",
        )
    return store, result.run_id


class PaperLifecycleServiceTests(unittest.TestCase):
    """The lifecycle commands (SP 4.84)."""

    def test_init_creates_draft_run(self) -> None:
        store, run_id = _store_with_run()
        row = store.get_run(run_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], PaperStatus.DRAFT.value)
        self.assertEqual(row["strategy"], "paper-demo")
        self.assertEqual(row["base_currency"], "HKD")
        self.assertEqual(row["markets"], ["HK"])

    def test_start_approves_and_activates(self) -> None:
        store, run_id = _store_with_run()
        result = paper_start_command(store=store, run_id=run_id, approver="risk-committee")  # type: ignore[arg-type]
        self.assertEqual(result.status, PaperStatus.ACTIVE.value)
        self.assertEqual(store.get_run(run_id)["status"], PaperStatus.ACTIVE.value)
        approvals = store.list_approvals(run_id)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["decision"], "APPROVED")

    def test_start_unknown_run_rejected(self) -> None:
        store = FakeStore()
        with self.assertRaises(PaperServiceError):
            paper_start_command(store=store, run_id="nope")  # type: ignore[arg-type]

    def test_stop_stops_run(self) -> None:
        store, run_id = _store_with_run()
        paper_start_command(store=store, run_id=run_id)  # type: ignore[arg-type]
        result = paper_stop_command(store=store, run_id=run_id)  # type: ignore[arg-type]
        self.assertEqual(result.status, PaperStatus.STOPPED.value)
        self.assertEqual(store.get_run(run_id)["status"], PaperStatus.STOPPED.value)

    def test_status_view(self) -> None:
        store, run_id = _store_with_run()
        status = paper_status_command(store=store, run_id=run_id)  # type: ignore[arg-type]
        self.assertEqual(status["run_id"], run_id)
        self.assertEqual(status["status"], PaperStatus.DRAFT.value)
        self.assertEqual(status["order_count"], 0)


class PaperSignalOrderServiceTests(unittest.TestCase):
    """The signal -> order pipeline (SP 4.85)."""

    def _started(self) -> tuple[FakeStore, str]:
        store, run_id = _store_with_run()
        paper_start_command(store=store, run_id=run_id)  # type: ignore[arg-type]
        return store, run_id

    def test_signal_derives_orders(self) -> None:
        store, run_id = self._started()
        result = paper_signal_command(  # type: ignore[arg-type]
            store=store,
            run_id=run_id,
            rebalance_date=_TRADE,
            targets={"0001.HK": 0.5},
            prices={"0001.HK": 50.0},
            source_run_id="oos-1",
        )
        self.assertEqual(result.order_count, 1)
        orders = paper_order_list_command(store=store, run_id=run_id)  # type: ignore[arg-type]
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["symbol"], "0001.HK")
        self.assertEqual(orders[0]["side"], "BUY")
        self.assertEqual(orders[0]["status"], "CREATED")
        # 0.5 * 1,000,000 / 50 = 10,000 shares (aligned to HK lots)
        self.assertEqual(orders[0]["quantity"], 10_000.0)

    def test_order_show(self) -> None:
        store, run_id = self._started()
        paper_signal_command(  # type: ignore[arg-type]
            store=store,
            run_id=run_id,
            rebalance_date=_TRADE,
            targets={"0001.HK": 0.5},
            prices={"0001.HK": 50.0},
        )
        orders = paper_order_list_command(store=store, run_id=run_id)  # type: ignore[arg-type]
        shown = paper_order_show_command(  # type: ignore[arg-type]
            store=store, run_id=run_id, order_id=orders[0]["order_id"]
        )
        self.assertEqual(shown["order_id"], orders[0]["order_id"])

    def test_below_one_lot_skipped(self) -> None:
        store, run_id = self._started()
        result = paper_signal_command(  # type: ignore[arg-type]
            store=store,
            run_id=run_id,
            rebalance_date=_TRADE,
            targets={"0001.HK": 0.0001},
            prices={"0001.HK": 50.0},
        )
        # 0.0001 * 1,000,000 / 50 = 2 shares < one 100-share lot -> skipped
        self.assertEqual(result.order_count, 0)
        self.assertEqual(len(result.skipped), 1)

    def test_signal_on_stopped_run_rejected(self) -> None:
        store, run_id = _store_with_run()
        paper_start_command(store=store, run_id=run_id)  # type: ignore[arg-type]
        paper_stop_command(store=store, run_id=run_id)  # type: ignore[arg-type]
        with self.assertRaises(PaperServiceError):
            paper_signal_command(  # type: ignore[arg-type]
                store=store,
                run_id=run_id,
                rebalance_date=_TRADE,
                targets={"0001.HK": 0.5},
                prices={"0001.HK": 50.0},
            )

    def test_missing_price_rejected(self) -> None:
        store, run_id = self._started()
        with self.assertRaises(PaperServiceError):
            paper_signal_command(  # type: ignore[arg-type]
                store=store,
                run_id=run_id,
                rebalance_date=_TRADE,
                targets={"0001.HK": 0.5},
                prices={},
            )

    def test_order_approve_and_reject(self) -> None:
        store, run_id = self._started()
        paper_signal_command(  # type: ignore[arg-type]
            store=store,
            run_id=run_id,
            rebalance_date=_TRADE,
            targets={"0001.HK": 0.5},
            prices={"0001.HK": 50.0},
        )
        orders = paper_order_list_command(store=store, run_id=run_id)  # type: ignore[arg-type]
        order_id = orders[0]["order_id"]
        approved = paper_approve_order_command(  # type: ignore[arg-type]
            store=store,
            run_id=run_id,
            order_id=order_id,
            approver="risk-committee",
            decision="APPROVED",
        )
        self.assertEqual(approved.status, "APPROVED")
        approvals = store.list_approvals(run_id)
        self.assertEqual(len(approvals), 2)  # run start + order approval


class PaperReconcileReportServiceTests(unittest.TestCase):
    """The reconcile and report commands (SP 4.87)."""

    def test_reconcile_without_snapshot_records_difference(self) -> None:
        store, run_id = _store_with_run()
        paper_start_command(store=store, run_id=run_id)  # type: ignore[arg-type]
        result = paper_reconcile_command(  # type: ignore[arg-type]
            store=store,
            run_id=run_id,
            as_of=_TRADE,
        )
        self.assertFalse(result.reconciled)
        self.assertEqual(result.difference_count, 1)
        self.assertEqual(len(store.list_reconciliation_differences(run_id)), 1)

    def test_reconcile_with_matching_snapshot(self) -> None:
        store, run_id = _store_with_run()
        paper_start_command(store=store, run_id=run_id)  # type: ignore[arg-type]
        store.insert_net_values(
            run_id,
            [
                {
                    "as_of_date": _TRADE,
                    "currency": "HKD",
                    "cash": 1_000_000.0,
                    "securities_value": 0.0,
                    "fees_paid": 0.0,
                    "total_value": 1_000_000.0,
                }
            ],
        )
        result = paper_reconcile_command(  # type: ignore[arg-type]
            store=store,
            run_id=run_id,
            as_of=_TRADE,
        )
        self.assertTrue(result.reconciled)
        self.assertEqual(result.difference_count, 0)

    def test_report_json(self) -> None:
        store, run_id = _store_with_run()
        paper_start_command(store=store, run_id=run_id)  # type: ignore[arg-type]
        output = paper_report_command(  # type: ignore[arg-type]
            store=store,
            run_id=run_id,
            report_format="json",
        )
        payload = json.loads(output)
        self.assertEqual(payload["status"]["run_id"], run_id)
        self.assertEqual(payload["orders"], [])

    def test_report_csv(self) -> None:
        store, run_id = _store_with_run()
        paper_start_command(store=store, run_id=run_id)  # type: ignore[arg-type]
        paper_signal_command(  # type: ignore[arg-type]
            store=store,
            run_id=run_id,
            rebalance_date=_TRADE,
            targets={"0001.HK": 0.5},
            prices={"0001.HK": 50.0},
        )
        output = paper_report_command(  # type: ignore[arg-type]
            store=store,
            run_id=run_id,
            report_format="csv",
        )
        self.assertIn("order_id,market,symbol", output)
        self.assertIn("0001.HK", output)

    def test_report_html(self) -> None:
        store, run_id = _store_with_run()
        output = paper_report_command(  # type: ignore[arg-type]
            store=store,
            run_id=run_id,
            report_format="html",
        )
        self.assertIn("<!doctype html>", output)
        self.assertIn("Paper run report", output)

    def test_report_unknown_format_rejected(self) -> None:
        store, run_id = _store_with_run()
        with self.assertRaises(PaperServiceError):
            paper_report_command(  # type: ignore[arg-type]
                store=store,
                run_id=run_id,
                report_format="xml",
            )


class PaperCliWiringTests(unittest.TestCase):
    """The CLI wiring for the paper subcommands (SP 4.84-4.87)."""

    ENVIRONMENT = {
        "DATABASE_URL": "postgresql+psycopg://harbor:secret@localhost:5432/harbor",
    }

    def _config_path(self) -> str:
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "paper.yaml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_PAPER_YAML)
        return path

    def test_paper_help_available(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, self.ENVIRONMENT, clear=True):
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_context:
                    main(["paper", "--help"])
        self.assertEqual(exit_context.exception.code, 0)
        self.assertIn("init", output.getvalue())
        self.assertIn("reconcile", output.getvalue())

    def test_paper_init_prints_run_id_and_status(self) -> None:
        class _Result:
            run_id = "paper-1"
            status = "DRAFT"

            def to_dict(self) -> dict[str, str]:
                return {"run_id": self.run_id, "status": self.status}

        config_path = self._config_path()
        output = io.StringIO()
        with patch.dict(os.environ, self.ENVIRONMENT, clear=True):
            with patch("harbor.cli.create_engine", return_value=MagicMock()):
                with patch("harbor.cli.paper_init_command", return_value=_Result()) as init_mock:
                    with redirect_stdout(output), redirect_stderr(io.StringIO()):
                        exit_code = main(
                            [
                                "paper",
                                "init",
                                "--config",
                                config_path,
                                "--dataset-fingerprint",
                                "dataset-abc",
                            ]
                        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(init_mock.call_count, 1)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["run_id"], "paper-1")
        self.assertEqual(summary["status"], "DRAFT")

    def test_paper_init_missing_config_is_usage_error(self) -> None:
        with patch.dict(os.environ, self.ENVIRONMENT, clear=True):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_context:
                    main(["paper", "init"])
        self.assertEqual(exit_context.exception.code, 2)

    def test_paper_status_prints_json(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, self.ENVIRONMENT, clear=True):
            with patch("harbor.cli.create_engine", return_value=MagicMock()):
                with patch(
                    "harbor.cli.paper_status_command",
                    return_value={"run_id": "paper-1", "status": "ACTIVE"},
                ):
                    with redirect_stdout(output), redirect_stderr(io.StringIO()):
                        exit_code = main(["paper", "status", "paper-1"])
        self.assertEqual(exit_code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["status"], "ACTIVE")

    def test_paper_signal_parses_targets(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, self.ENVIRONMENT, clear=True):
            with patch("harbor.cli.create_engine", return_value=MagicMock()):
                with patch(
                    "harbor.cli.paper_signal_command",
                    return_value=MagicMock(to_dict=lambda: {"run_id": "paper-1", "order_count": 1}),
                ) as signal_mock:
                    with redirect_stdout(output), redirect_stderr(io.StringIO()):
                        exit_code = main(
                            [
                                "paper",
                                "signal",
                                "paper-1",
                                "--rebalance-date",
                                "2026-01-02",
                                "--target",
                                "0001.HK:0.5",
                                "--price",
                                "0001.HK:50.0",
                            ]
                        )
        self.assertEqual(exit_code, 0)
        kwargs = signal_mock.call_args.kwargs
        self.assertEqual(kwargs["targets"], {"0001.HK": 0.5})
        self.assertEqual(kwargs["prices"], {"0001.HK": 50.0})

    def test_paper_report_defaults_to_json(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, self.ENVIRONMENT, clear=True):
            with patch("harbor.cli.create_engine", return_value=MagicMock()):
                with patch("harbor.cli.paper_report_command", return_value='{"ok": true}'):
                    with redirect_stdout(output), redirect_stderr(io.StringIO()):
                        exit_code = main(["paper", "report", "paper-1"])
        self.assertEqual(exit_code, 0)
        self.assertIn("ok", output.getvalue())

    def test_paper_unknown_subcommand_is_usage_error(self) -> None:
        with patch.dict(os.environ, self.ENVIRONMENT, clear=True):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_context:
                    main(["paper", "export", "paper-1"])
        self.assertEqual(exit_context.exception.code, 2)
