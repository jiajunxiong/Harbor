"""Backtest resume/cancel policy tests (MVP 2 / SP 2.70).

Verifies the pure resume policy: runs still in progress are rejected, completed
runs are reused (SP 2.48) and failed/cancelled runs resume as a new run linked
to the original. Also covers the cancel gate backed by the SP 2.46 state
machine. No database is required.
"""

import unittest
from dataclasses import FrozenInstanceError

from harbor.core.backtest_domain import BacktestStatus
from harbor.core.resume_policy import (
    ResumeAction,
    ResumeDecision,
    ResumePolicyError,
    can_cancel,
    decide_resume,
)


class ResumeDecisionTests(unittest.TestCase):
    """Verify the frozen decision record and its readable rendering."""

    def test_decision_is_frozen(self) -> None:
        decision = ResumeDecision(
            run_id="run-1",
            status=BacktestStatus.FAILED,
            action=ResumeAction.NEW_RUN,
            new_run_id="run-2",
            reason="never silently continue.",
        )
        with self.assertRaises(FrozenInstanceError):
            decision.action = ResumeAction.REUSE  # type: ignore[misc]

    def test_readable_includes_all_fields(self) -> None:
        decision = ResumeDecision(
            run_id="run-1",
            status=BacktestStatus.CANCELLED,
            action=ResumeAction.NEW_RUN,
            new_run_id="run-2",
            reason="a fresh run linked to the original.",
        )
        text = decision.readable()
        self.assertIn("run-1", text)
        self.assertIn("CANCELLED", text)
        self.assertIn("new_run", text)
        self.assertIn("run-2", text)
        self.assertIn("a fresh run linked to the original.", text)

    def test_readable_without_new_run_uses_placeholder(self) -> None:
        decision = ResumeDecision(
            run_id="run-1",
            status=BacktestStatus.COMPLETED,
            action=ResumeAction.REUSE,
            new_run_id=None,
            reason="already completed.",
        )
        self.assertIn("—", decision.readable())


class DecideResumeTests(unittest.TestCase):
    """Verify the resume decision per status."""

    def test_initializing_is_rejected_and_tells_user_to_cancel(self) -> None:
        decision = decide_resume(run_id="run-1", status=BacktestStatus.INITIALIZING)
        self.assertEqual(decision.action, ResumeAction.REJECT)
        self.assertIsNone(decision.new_run_id)
        self.assertIn("cancel", decision.reason.lower())

    def test_running_is_rejected_and_tells_user_to_cancel(self) -> None:
        decision = decide_resume(run_id="run-1", status=BacktestStatus.RUNNING)
        self.assertEqual(decision.action, ResumeAction.REJECT)
        self.assertIsNone(decision.new_run_id)
        self.assertIn("cancel", decision.reason.lower())

    def test_completed_is_reused(self) -> None:
        decision = decide_resume(run_id="run-1", status=BacktestStatus.COMPLETED)
        self.assertEqual(decision.action, ResumeAction.REUSE)
        self.assertIsNone(decision.new_run_id)

    def test_failed_creates_new_run_with_link(self) -> None:
        decision = decide_resume(run_id="run-1", status=BacktestStatus.FAILED, new_run_id="run-2")
        self.assertEqual(decision.action, ResumeAction.NEW_RUN)
        self.assertEqual(decision.new_run_id, "run-2")
        self.assertIn("new run", decision.reason.lower())

    def test_cancelled_creates_new_run_with_link(self) -> None:
        decision = decide_resume(
            run_id="run-1", status=BacktestStatus.CANCELLED, new_run_id="run-2"
        )
        self.assertEqual(decision.action, ResumeAction.NEW_RUN)
        self.assertEqual(decision.new_run_id, "run-2")
        self.assertIn("new run", decision.reason.lower())

    def test_new_run_id_defaults_to_none(self) -> None:
        decision = decide_resume(run_id="run-1", status=BacktestStatus.CANCELLED)
        self.assertEqual(decision.action, ResumeAction.NEW_RUN)
        self.assertIsNone(decision.new_run_id)

    def test_unknown_status_raises_policy_error(self) -> None:
        with self.assertRaises(ResumePolicyError):
            decide_resume(run_id="run-1", status="MYSTERY")  # type: ignore[arg-type]


class CanCancelTests(unittest.TestCase):
    """Verify the cancel gate follows the SP 2.46 state machine."""

    def test_in_progress_runs_can_be_cancelled(self) -> None:
        self.assertTrue(can_cancel(BacktestStatus.INITIALIZING))
        self.assertTrue(can_cancel(BacktestStatus.RUNNING))

    def test_terminal_runs_cannot_be_cancelled(self) -> None:
        self.assertFalse(can_cancel(BacktestStatus.COMPLETED))
        self.assertFalse(can_cancel(BacktestStatus.FAILED))
        self.assertFalse(can_cancel(BacktestStatus.CANCELLED))


class ResumeActionTests(unittest.TestCase):
    """Verify the action enum values."""

    def test_enum_values(self) -> None:
        self.assertEqual(ResumeAction.NEW_RUN.value, "new_run")
        self.assertEqual(ResumeAction.REUSE.value, "reuse")
        self.assertEqual(ResumeAction.REJECT.value, "reject")
