"""Recovery flow tests (MVP 4 / SP 4.42).

Verifies that a run recovers only after an independent review passes and that
the review and recovery approval are archived together.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.paper_circuit_breakers import evaluate_monthly_breaker
from harbor.core.paper_domain import ApprovalDecision, RiskApproval
from harbor.core.paper_recovery import (
    PaperRecoveryError,
    RecoveryRecord,
    RecoveryReview,
    recover_run,
    recovery_review_required,
    require_recovery_review,
)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _review(passed: bool = True, **overrides: object) -> RecoveryReview:
    fields: dict[str, object] = {
        "paper_run_id": "paper-1",
        "reviewer": "bob",
        "reviewed_at": _utc_at(2026, 1, 5, 9),
        "passed": passed,
        "notes": "independent review completed" if passed else "review failed",
    }
    fields.update(overrides)
    return RecoveryReview(**fields)  # type: ignore[arg-type]


def _approval() -> RiskApproval:
    return RiskApproval(
        approval_id="ap-recovery",
        paper_run_id="paper-1",
        scope="run:paper-1",
        approver="carol",
        decision=ApprovalDecision.APPROVED,
        rule="recovery_approval",
        reason="recovery approved after review",
        decided_at=_utc_at(2026, 1, 5, 10),
    )


class RecoveryReviewTests(unittest.TestCase):
    """The independent review (SP 4.42)."""

    def test_review_required_for_monthly_breaker(self) -> None:
        assessment = evaluate_monthly_breaker(as_of=date(2026, 1, 15), monthly_loss_pct=0.12)
        self.assertTrue(recovery_review_required(assessment))

    def test_review_not_required_for_clear_breaker(self) -> None:
        assessment = evaluate_monthly_breaker(as_of=date(2026, 1, 15), monthly_loss_pct=0.05)
        self.assertFalse(recovery_review_required(assessment))

    def test_require_review_passes(self) -> None:
        require_recovery_review(_review(passed=True))

    def test_require_review_failed_raises(self) -> None:
        with self.assertRaises(PaperRecoveryError):
            require_recovery_review(_review(passed=False))

    def test_failed_review_requires_notes(self) -> None:
        with self.assertRaises(PaperRecoveryError):
            _review(passed=False, notes="")

    def test_readable(self) -> None:
        self.assertIn("PASSED", _review().readable())


class RecoverRunTests(unittest.TestCase):
    """Archiving the recovery (SP 4.42)."""

    def test_recover_after_passed_review(self) -> None:
        record = recover_run(review=_review(passed=True), approval=_approval())
        self.assertIsInstance(record, RecoveryRecord)
        self.assertEqual(record.paper_run_id, "paper-1")
        self.assertIn("recovery archived", record.readable())

    def test_recover_failed_review_rejected(self) -> None:
        with self.assertRaises(PaperRecoveryError):
            recover_run(review=_review(passed=False), approval=_approval())

    def test_recover_without_approval_rejected(self) -> None:
        rejected = RiskApproval(
            approval_id="ap-recovery",
            paper_run_id="paper-1",
            scope="run:paper-1",
            approver="carol",
            decision=ApprovalDecision.REJECTED,
            rule="recovery_approval",
            reason="not approved",
            decided_at=_utc_at(2026, 1, 5, 10),
        )
        with self.assertRaises(PaperRecoveryError):
            recover_run(review=_review(passed=True), approval=rejected)

    def test_review_run_mismatch_rejected(self) -> None:
        record = RecoveryRecord(
            paper_run_id="paper-1",
            review=_review(passed=True),
            approval=_approval(),
            recovered_at=_utc_at(2026, 1, 5, 11),
        )
        with self.assertRaises(PaperRecoveryError):
            # reconstruct with a mismatched review run id
            RecoveryRecord(
                paper_run_id="paper-other",
                review=record.review,
                approval=record.approval,
                recovered_at=record.recovered_at,
            )

    def test_naive_recovery_time_rejected(self) -> None:
        with self.assertRaises(ValueError):
            recover_run(
                review=_review(passed=True),
                approval=_approval(),
                recovered_at=datetime(2026, 1, 5, 11),
            )
