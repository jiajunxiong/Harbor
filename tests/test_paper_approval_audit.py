"""Approval audit suite (MVP 4 / SP 4.48).

Verifies that manual approvals and rejections are recorded with the approver /
reason / time and that the audit trail replays identically.
"""

import unittest
from datetime import datetime, timezone

from harbor.core.paper_approval import ApprovalScope, ApprovalWorkflow, RiskLevel
from harbor.core.paper_audit import (
    AuditActor,
    AuditEventType,
    audit_fingerprint,
    new_paper_audit_log,
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
        reason="high-risk order",
        requested_at=_utc_at(2026, 1, 2, 9),
    )


class ApprovalAuditSuiteTests(unittest.TestCase):
    """The approval audit trail (SP 4.48)."""

    def _recorded_log(self, decision: ApprovalDecision) -> object:
        workflow = _workflow().decide(
            request_id="req-1",
            decision=decision,
            approver="alice",
            reason="reviewed",
            decided_at=_utc_at(2026, 1, 2, 10),
        )
        record = workflow.decision_for("req-1")
        log = new_paper_audit_log("paper-1", recorded_at=_utc_at(2026, 1, 1, 8))
        return log.append(
            event_id="e-approval",
            event_type=AuditEventType.APPROVAL,
            actor=AuditActor.APPROVER,
            detail=record.readable() if record is not None else "approved",
            recorded_at=_utc_at(2026, 1, 2, 10),
        )

    def test_approval_recorded_with_approver(self) -> None:
        workflow = _workflow().decide(
            request_id="req-1",
            decision=ApprovalDecision.APPROVED,
            approver="alice",
            reason="reviewed and accepted",
            decided_at=_utc_at(2026, 1, 2, 10),
        )
        record = workflow.decision_for("req-1")
        self.assertIsNotNone(record)
        self.assertEqual(record.approver, "alice")
        self.assertEqual(record.decision, ApprovalDecision.APPROVED)
        self.assertIn("alice", record.readable())

    def test_rejection_recorded(self) -> None:
        workflow = _workflow().decide(
            request_id="req-1",
            decision=ApprovalDecision.REJECTED,
            approver="alice",
            reason="too concentrated",
            decided_at=_utc_at(2026, 1, 2, 10),
        )
        record = workflow.decision_for("req-1")
        self.assertEqual(record.decision, ApprovalDecision.REJECTED)
        self.assertIn("too concentrated", record.readable())

    def test_audit_log_records_approval_event(self) -> None:
        log = self._recorded_log(ApprovalDecision.APPROVED)
        events = log.events_for(AuditEventType.APPROVAL)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].actor, AuditActor.APPROVER)

    def test_audit_replays_identically(self) -> None:
        first = self._recorded_log(ApprovalDecision.APPROVED)
        second = self._recorded_log(ApprovalDecision.APPROVED)
        self.assertEqual(audit_fingerprint(first), audit_fingerprint(second))

    def test_approval_changes_fingerprint(self) -> None:
        approved = self._recorded_log(ApprovalDecision.APPROVED)
        rejected = self._recorded_log(ApprovalDecision.REJECTED)
        self.assertNotEqual(audit_fingerprint(approved), audit_fingerprint(rejected))
