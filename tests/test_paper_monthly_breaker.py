"""Monthly breaker suite (MVP 4 / SP 4.45).

Verifies the monthly circuit-breaker chain: a month-to-date loss (accumulated
across days) trips the breaker, requires an independent review, and recovery
only follows a passed review.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.paper_circuit_breakers import evaluate_monthly_breaker
from harbor.core.paper_domain import ApprovalDecision, RiskApproval
from harbor.core.paper_recovery import (
    RecoveryReview,
    recover_run,
    recovery_review_required,
)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


class MonthlyBreakerSuiteTests(unittest.TestCase):
    """The monthly breaker chain (SP 4.45)."""

    def test_cross_day_accumulation_trips(self) -> None:
        # two daily losses accumulate into a month-to-date loss above the limit
        daily_losses = [0.06, 0.05]
        month_to_date = sum(daily_losses)
        self.assertGreater(month_to_date, 0.10)
        assessment = evaluate_monthly_breaker(
            as_of=date(2026, 1, 31), monthly_loss_pct=month_to_date
        )
        self.assertTrue(assessment.triggered)
        self.assertTrue(assessment.requires_review)
        self.assertTrue(recovery_review_required(assessment))

    def test_within_limit_does_not_trip(self) -> None:
        assessment = evaluate_monthly_breaker(as_of=date(2026, 1, 31), monthly_loss_pct=0.07)
        self.assertFalse(assessment.triggered)
        self.assertFalse(recovery_review_required(assessment))

    def test_recovery_requires_independent_review(self) -> None:
        assessment = evaluate_monthly_breaker(as_of=date(2026, 1, 31), monthly_loss_pct=0.12)
        self.assertTrue(recovery_review_required(assessment))
        review = RecoveryReview(
            paper_run_id="paper-1",
            reviewer="bob",
            reviewed_at=_utc_at(2026, 2, 3, 9),
            passed=True,
            notes="independent review completed",
        )
        approval = RiskApproval(
            approval_id="ap-recovery",
            paper_run_id="paper-1",
            scope="run:paper-1",
            approver="carol",
            decision=ApprovalDecision.APPROVED,
            rule="recovery_approval",
            reason="approved",
            decided_at=_utc_at(2026, 2, 3, 10),
        )
        record = recover_run(review=review, approval=approval)
        self.assertEqual(record.paper_run_id, "paper-1")
        self.assertIn("recovery archived", record.readable())

    def test_recovery_without_passed_review_blocked(self) -> None:
        from harbor.core.paper_recovery import PaperRecoveryError, recover_run

        review = RecoveryReview(
            paper_run_id="paper-1",
            reviewer="bob",
            reviewed_at=_utc_at(2026, 2, 3, 9),
            passed=False,
            notes="review did not pass",
        )
        approval = RiskApproval(
            approval_id="ap-recovery",
            paper_run_id="paper-1",
            scope="run:paper-1",
            approver="carol",
            decision=ApprovalDecision.APPROVED,
            rule="recovery_approval",
            reason="approved",
            decided_at=_utc_at(2026, 2, 3, 10),
        )
        with self.assertRaises(PaperRecoveryError):
            recover_run(review=review, approval=approval)
