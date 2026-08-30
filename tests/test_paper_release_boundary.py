"""Paper release boundary review tests (MVP 4 / SP 4.95).

Confirms the paper loop never crosses into execution before release: the paper
path produces no broker orders / credentials, its reports carry no return or
drawdown promise, and it cannot be upgraded to MVP 5 without a passed
difference verification (SP 4.83 / 4.95). Uses the SP 4.83 research boundary
review over an in-memory paper run and its report output.
"""

import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from harbor.core.oos_conclusion import no_return_promise_statement
from harbor.core.paper_research_boundary import (
    ResearchBoundaryReview,
    review_research_boundary,
)
from harbor.services.paper import (
    paper_init_command,
    paper_report_command,
    paper_start_command,
)

_PAPER_YAML = """\
strategy: paper-demo
strategy_version: "1.0.0"
description: "research only — no advice"
markets:
  - HK
base_currency: HKD
currencies:
  - HKD
initial_capital: 1000000
"""

_AS_OF = date(2026, 1, 15)


class _FakeStore:
    """The minimal in-memory store the paper service needs for a report."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, object]] = {}
        self.orders: list[dict[str, object]] = []
        self.approvals: list[dict[str, object]] = []
        self.differences: list[dict[str, object]] = []
        self.fills: list[dict[str, object]] = []

    def create_run(self, **kwargs: object) -> int:
        self.runs[str(kwargs["run_id"])] = dict(kwargs)
        return 1

    def get_run(self, run_id: str) -> dict[str, object] | None:
        return self.runs.get(run_id)

    def update_run(self, *, run_id: str, status: str, started_at=None, stopped_at=None) -> int:
        self.runs[run_id]["status"] = status
        return 1

    def insert_approval(self, paper_run_id: str, approval: object) -> int:
        self.approvals.append(approval)
        return 1

    def list_approvals(self, paper_run_id: str) -> list[dict[str, object]]:
        return [
            {
                "approval_id": "ap-1",
                "scope": "run",
                "approver": "system",
                "decision": "APPROVED",
                "rule": "paper_start",
                "decided_at": datetime.now(timezone.utc),
                "reason": None,
            }
        ]

    def insert_orders(self, paper_run_id: str, orders: object) -> int:
        self.orders.extend(orders)  # type: ignore[arg-type]
        return len(self.orders)

    def list_orders(self, paper_run_id: str) -> list[dict[str, object]]:
        return self.orders

    def insert_fills(self, paper_run_id: str, fills: object) -> int:
        self.fills.extend(fills)  # type: ignore[arg-type]
        return len(self.fills)

    def list_fills(self, paper_run_id: str) -> list[dict[str, object]]:
        return self.fills

    def insert_net_values(self, paper_run_id: str, rows: object) -> int:
        return len(rows)  # type: ignore[arg-type]

    def list_net_values(self, paper_run_id: str) -> list[dict[str, object]]:
        return []

    def insert_reconciliation_differences(self, paper_run_id: str, rows: object) -> int:
        return len(rows)  # type: ignore[arg-type]

    def list_reconciliation_differences(self, paper_run_id: str) -> list[dict[str, object]]:
        return []


class PaperReleaseBoundaryTests(unittest.TestCase):
    """The pre-release boundary review (SP 4.95)."""

    def setUp(self) -> None:
        self.store = _FakeStore()
        directory = __import__("tempfile", fromlist=["TemporaryDirectory"])
        with directory.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper.yaml"
            path.write_text(_PAPER_YAML, encoding="utf-8")
            self.run_id = paper_init_command(
                config_path=str(path),
                store=self.store,  # type: ignore[arg-type]
                code_version="1.0.0",
                dataset_fingerprint="dataset-abc",
            ).run_id
        paper_start_command(store=self.store, run_id=self.run_id)  # type: ignore[arg-type]

    def test_boundary_passes_when_all_conditions_met(self) -> None:
        review = review_research_boundary(
            paper_run_id=self.run_id,
            reviewed_at=_AS_OF,
            reviewer="ops-owner",
            no_broker_orders=True,
            no_return_promise=True,
            admission_passed=True,
        )
        self.assertIsInstance(review, ResearchBoundaryReview)
        self.assertTrue(review.passed)

    def test_broker_orders_block_release(self) -> None:
        review = review_research_boundary(
            paper_run_id=self.run_id,
            reviewed_at=_AS_OF,
            reviewer="ops-owner",
            no_broker_orders=False,
            no_return_promise=True,
            admission_passed=True,
        )
        self.assertFalse(review.passed)

    def test_return_promise_blocks_release(self) -> None:
        review = review_research_boundary(
            paper_run_id=self.run_id,
            reviewed_at=_AS_OF,
            reviewer="ops-owner",
            no_broker_orders=True,
            no_return_promise=False,
            admission_passed=True,
        )
        self.assertFalse(review.passed)

    def test_unpassed_admission_blocks_release(self) -> None:
        review = review_research_boundary(
            paper_run_id=self.run_id,
            reviewed_at=_AS_OF,
            reviewer="ops-owner",
            no_broker_orders=True,
            no_return_promise=True,
            admission_passed=False,
        )
        self.assertFalse(review.passed)

    def test_report_contains_no_return_promise(self) -> None:
        output = paper_report_command(  # type: ignore[arg-type]
            store=self.store,
            run_id=self.run_id,
            report_format="json",
        )
        payload = json.loads(output)
        text = json.dumps(payload)
        # the paper report is a research record, never a promise
        self.assertNotIn("guarantee", text)
        self.assertNotIn("promise", text)
        # the standard no-return-promise statement (SP 3.64) is reused
        self.assertIn("no projection", no_return_promise_statement())

    def test_paper_report_has_no_broker_fields(self) -> None:
        output = paper_report_command(  # type: ignore[arg-type]
            store=self.store,
            run_id=self.run_id,
            report_format="json",
        )
        payload = json.loads(output)
        self.assertNotIn("broker", json.dumps(payload).lower())
        self.assertNotIn("credentials", json.dumps(payload).lower())

    def test_boundary_required_for_upgrade(self) -> None:
        # without a passed admission the run cannot be upgraded to MVP 5
        review = review_research_boundary(
            paper_run_id=self.run_id,
            reviewed_at=_AS_OF,
            reviewer="ops-owner",
            no_broker_orders=True,
            no_return_promise=True,
            admission_passed=False,
        )
        self.assertFalse(review.passed)
        self.assertIn("BLOCKED", review.readable())
