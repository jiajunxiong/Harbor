"""Backtest state machine tests (MVP 2 / SP 2.46).

Verifies the lifecycle transitions (INITIALIZING -> RUNNING -> COMPLETED, with
FAILED / CANCELLED), that terminal states cannot transition, and that a failed
run retains its diagnostics (error summary, stage and accumulated warnings).
"""

import unittest

from harbor.core.backtest_domain import BacktestStatus
from harbor.core.backtest_state_machine import (
    RunDiagnostics,
    RunState,
    StateTransitionError,
    allowed_transitions,
    can_transition,
    initial_state,
)


class TransitionTests(unittest.TestCase):
    """Verify valid and invalid lifecycle transitions."""

    def test_initial_state_is_initializing(self) -> None:
        state = initial_state("run-1")
        self.assertEqual(state.status, BacktestStatus.INITIALIZING)
        self.assertEqual(state.run_id, "run-1")

    def test_start_moves_to_running(self) -> None:
        self.assertEqual(initial_state("run-1").start().status, BacktestStatus.RUNNING)

    def test_run_completes(self) -> None:
        state = initial_state("run-1").start().complete()
        self.assertEqual(state.status, BacktestStatus.COMPLETED)

    def test_run_fails(self) -> None:
        state = initial_state("run-1").start().fail("bad data")
        self.assertEqual(state.status, BacktestStatus.FAILED)

    def test_run_cancels(self) -> None:
        state = initial_state("run-1").start().cancel()
        self.assertEqual(state.status, BacktestStatus.CANCELLED)

    def test_initializing_can_fail(self) -> None:
        state = initial_state("run-1").fail("preflight failed")
        self.assertEqual(state.status, BacktestStatus.FAILED)

    def test_initializing_can_cancel(self) -> None:
        state = initial_state("run-1").cancel()
        self.assertEqual(state.status, BacktestStatus.CANCELLED)

    def test_completed_cannot_transition(self) -> None:
        state = initial_state("run-1").start().complete()
        with self.assertRaisesRegex(StateTransitionError, "COMPLETED -> RUNNING"):
            state.transition(BacktestStatus.RUNNING)

    def test_failed_cannot_resume(self) -> None:
        state = initial_state("run-1").start().fail("boom")
        with self.assertRaisesRegex(StateTransitionError, "FAILED -> RUNNING"):
            state.start()

    def test_cancelled_is_terminal(self) -> None:
        state = initial_state("run-1").cancel()
        with self.assertRaisesRegex(StateTransitionError, "CANCELLED -> COMPLETED"):
            state.transition(BacktestStatus.COMPLETED)

    def test_initializing_cannot_complete_directly(self) -> None:
        with self.assertRaisesRegex(StateTransitionError, "INITIALIZING -> COMPLETED"):
            initial_state("run-1").transition(BacktestStatus.COMPLETED)

    def test_can_transition_helper(self) -> None:
        self.assertTrue(can_transition(BacktestStatus.INITIALIZING, BacktestStatus.RUNNING))
        self.assertTrue(can_transition(BacktestStatus.RUNNING, BacktestStatus.FAILED))
        self.assertFalse(can_transition(BacktestStatus.COMPLETED, BacktestStatus.RUNNING))
        self.assertFalse(can_transition(BacktestStatus.FAILED, BacktestStatus.RUNNING))

    def test_allowed_transitions(self) -> None:
        self.assertEqual(
            allowed_transitions(BacktestStatus.RUNNING),
            frozenset({BacktestStatus.COMPLETED, BacktestStatus.FAILED, BacktestStatus.CANCELLED}),
        )
        self.assertEqual(allowed_transitions(BacktestStatus.COMPLETED), frozenset())


class DiagnosticsTests(unittest.TestCase):
    """Verify a failed run retains diagnostics (SP 2.46)."""

    def test_with_warning_accumulates(self) -> None:
        state = initial_state("run-1").start().with_warning("stale price")
        self.assertIn("stale price", state.diagnostics.warnings)
        self.assertEqual(state.status, BacktestStatus.RUNNING)

    def test_fail_retains_warnings_error_and_stage(self) -> None:
        state = initial_state("run-1").start()
        state = state.with_warning("stale price").with_warning("missing fx")
        failed = state.fail("corporate action error", stage="corporate_action")
        self.assertEqual(failed.status, BacktestStatus.FAILED)
        self.assertEqual(failed.diagnostics.error_summary, "corporate action error")
        self.assertEqual(failed.diagnostics.stage, "corporate_action")
        self.assertEqual(failed.diagnostics.warnings, ("stale price", "missing fx"))

    def test_fail_requires_error_summary(self) -> None:
        with self.assertRaisesRegex(ValueError, "error_summary"):
            initial_state("run-1").start().fail("")

    def test_diagnostics_readable(self) -> None:
        diagnostics = RunDiagnostics(error_summary="boom", warnings=("w1",), stage="valuation")
        summary = diagnostics.readable()
        self.assertIn("stage: valuation", summary)
        self.assertIn("error: boom", summary)
        self.assertIn("warning: w1", summary)

    def test_state_is_immutable(self) -> None:
        state = initial_state("run-1")
        state.start()
        self.assertEqual(state.status, BacktestStatus.INITIALIZING)


class RunStateValidationTests(unittest.TestCase):
    """Verify run state validation and rendering."""

    def test_empty_run_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "run_id"):
            RunState(run_id="", status=BacktestStatus.INITIALIZING)

    def test_readable(self) -> None:
        state = initial_state("run-1").start().with_warning("stale price")
        summary = state.readable()
        self.assertIn("run run-1: RUNNING", summary)
        self.assertIn("warning: stale price", summary)


if __name__ == "__main__":
    unittest.main()
