"""Human approval workflow tests (MVP 4 / SP 4.39).

Verifies that high-risk orders/strategies require human approval, that the
approval record carries the approver/reason/time, and that rejected or
undecided high-risk requests are blocked.
"""

import unittest
from datetime import datetime, timezone

from harbor.core.paper_approval import (
    ApprovalRequest,
    ApprovalScope,
    ApprovalWorkflow,
    PaperApprovalError,
    RiskLevel,
)
from harbor.core.paper_domain import ApprovalDecision


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _workflow() -> ApprovalWorkflow:
    return ApprovalWorkflow().request(
        request_id="req-1",
        paper_run_id="paper-1",
        scope=ApprovalScope.ORDER,
        scope_id="order-1",
        risk_level=RiskLevel.HIGH,
        reason="order exceeds per-trade risk limit",
        requested_at=_utc_at(2026, 1, 2, 9),
    )


class ApprovalRequestTests(unittest.TestCase):
    """The approval request (SP 4.39)."""

    def test_valid_request(self) -> None:
        request = ApprovalRequest(
            request_id="req-1",
            paper_run_id="paper-1",
            scope=ApprovalScope.RUN,
            scope_id="paper-1",
            risk_level=RiskLevel.HIGH,
            reason="run activation approval",
            requested_at=_utc_at(2026, 1, 1, 8),
        )
        self.assertIn("approval request req-1", request.readable())

    def test_invalid_request(self) -> None:
        with self.assertRaises(PaperApprovalError):
            ApprovalRequest(
                request_id="",
                paper_run_id="p",
                scope=ApprovalScope.ORDER,
                scope_id="o",
                risk_level=RiskLevel.NORMAL,
                reason="x",
                requested_at=_utc_at(2026, 1, 1, 8),
            )
        with self.assertRaises(ValueError):
            ApprovalRequest(
                request_id="r",
                paper_run_id="p",
                scope=ApprovalScope.ORDER,
                scope_id="o",
                risk_level=RiskLevel.NORMAL,
                reason="x",
                requested_at=datetime(2026, 1, 1, 8),
            )


class ApprovalWorkflowTests(unittest.TestCase):
    """The workflow gate (SP 4.39)."""

    def test_normal_request_passes_without_approval(self) -> None:
        workflow = ApprovalWorkflow().request(
            request_id="req-normal",
            paper_run_id="paper-1",
            scope=ApprovalScope.ORDER,
            scope_id="order-2",
            risk_level=RiskLevel.NORMAL,
            reason="routine order",
            requested_at=_utc_at(2026, 1, 2, 9),
        )
        workflow.require_approved("req-normal")  # does not raise

    def test_high_request_requires_approval(self) -> None:
        workflow = _workflow()
        self.assertTrue(workflow.requires_approval("req-1"))
        with self.assertRaises(PaperApprovalError):
            workflow.require_approved("req-1")

    def test_high_request_approved_passes(self) -> None:
        workflow = _workflow().decide(
            request_id="req-1",
            decision=ApprovalDecision.APPROVED,
            approver="alice",
            reason="reviewed and accepted",
            decided_at=_utc_at(2026, 1, 2, 10),
        )
        workflow.require_approved("req-1")
        decision = workflow.decision_for("req-1")
        self.assertIsNotNone(decision)
        self.assertEqual(decision.approver, "alice")
        self.assertIn("alice", decision.readable())

    def test_high_request_rejected_blocked(self) -> None:
        workflow = _workflow().decide(
            request_id="req-1",
            decision=ApprovalDecision.REJECTED,
            approver="alice",
            reason="too concentrated",
            decided_at=_utc_at(2026, 1, 2, 10),
        )
        with self.assertRaises(PaperApprovalError):
            workflow.require_approved("req-1")

    def test_decide_unknown_request_rejected(self) -> None:
        workflow = _workflow()
        with self.assertRaises(PaperApprovalError):
            workflow.decide(
                request_id="req-nope",
                decision=ApprovalDecision.APPROVED,
                approver="alice",
                reason="x",
                decided_at=_utc_at(2026, 1, 2, 10),
            )

    def test_immutable(self) -> None:
        original = _workflow()
        updated = original.decide(
            request_id="req-1",
            decision=ApprovalDecision.APPROVED,
            approver="alice",
            reason="ok",
            decided_at=_utc_at(2026, 1, 2, 10),
        )
        self.assertEqual(original.decisions, ())
        self.assertEqual(len(updated.decisions), 1)

    def test_readable(self) -> None:
        workflow = _workflow()
        rendered = workflow.readable()
        self.assertIn("approval workflow: 1 request(s)", rendered)
        self.assertIn("req-1", rendered)
