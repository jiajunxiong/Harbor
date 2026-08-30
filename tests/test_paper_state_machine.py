"""Paper state machine tests (MVP 4 / SP 4.10).

Verifies the legal transitions over the six paper statuses (DRAFT, APPROVED,
ACTIVE, PAUSED, CIRCUIT_BROKEN, STOPPED), that illegal transitions are
rejected, and that every transition is recorded in a self-validating UTC
audit trail.
"""

import unittest
from datetime import datetime, timedelta, timezone

from harbor.core.paper_domain import PaperStatus
from harbor.core.paper_state_machine import (
    PaperDiagnostics,
    PaperRunState,
    PaperStateError,
    PaperTransition,
    allowed_transitions,
    can_transition,
    paper_initial_state,
)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _offset_at(hours: int) -> datetime:
    return datetime(2026, 1, 2, 9, tzinfo=timezone(timedelta(hours=hours)))


class TransitionRulesTests(unittest.TestCase):
    """The legal transition table (SP 4.10)."""

    def test_draft_allowed(self) -> None:
        self.assertEqual(
            allowed_transitions(PaperStatus.DRAFT),
            frozenset({PaperStatus.APPROVED, PaperStatus.STOPPED}),
        )
        self.assertTrue(can_transition(PaperStatus.DRAFT, PaperStatus.APPROVED))
        self.assertFalse(can_transition(PaperStatus.DRAFT, PaperStatus.ACTIVE))

    def test_approved_allowed(self) -> None:
        self.assertTrue(can_transition(PaperStatus.APPROVED, PaperStatus.ACTIVE))
        self.assertFalse(can_transition(PaperStatus.APPROVED, PaperStatus.PAUSED))

    def test_active_allowed(self) -> None:
        for target in (PaperStatus.PAUSED, PaperStatus.CIRCUIT_BROKEN, PaperStatus.STOPPED):
            self.assertTrue(can_transition(PaperStatus.ACTIVE, target))
        self.assertFalse(can_transition(PaperStatus.ACTIVE, PaperStatus.DRAFT))

    def test_paused_allowed(self) -> None:
        self.assertTrue(can_transition(PaperStatus.PAUSED, PaperStatus.ACTIVE))
        self.assertTrue(can_transition(PaperStatus.PAUSED, PaperStatus.STOPPED))
        self.assertFalse(can_transition(PaperStatus.PAUSED, PaperStatus.CIRCUIT_BROKEN))

    def test_circuit_broken_allowed(self) -> None:
        self.assertTrue(can_transition(PaperStatus.CIRCUIT_BROKEN, PaperStatus.ACTIVE))
        self.assertTrue(can_transition(PaperStatus.CIRCUIT_BROKEN, PaperStatus.STOPPED))
        self.assertFalse(can_transition(PaperStatus.CIRCUIT_BROKEN, PaperStatus.PAUSED))

    def test_stopped_is_terminal(self) -> None:
        self.assertEqual(allowed_transitions(PaperStatus.STOPPED), frozenset())


class PaperRunStateTests(unittest.TestCase):
    """Advancing the run state with an audit trail (SP 4.10)."""

    def test_full_lifecycle(self) -> None:
        state = paper_initial_state("paper-1")
        self.assertEqual(state.status, PaperStatus.DRAFT)
        state = state.approve(recorded_at=_utc_at(2026, 1, 1, 8))
        state = state.activate(recorded_at=_utc_at(2026, 1, 1, 9))
        state = state.pause(recorded_at=_utc_at(2026, 1, 2, 9))
        state = state.activate(recorded_at=_utc_at(2026, 1, 2, 10))
        state = state.stop(recorded_at=_utc_at(2026, 1, 3, 9))
        self.assertEqual(state.status, PaperStatus.STOPPED)
        self.assertEqual(len(state.transitions), 5)
        self.assertEqual(state.transitions[0].from_status, PaperStatus.DRAFT)
        self.assertEqual(state.transitions[-1].to_status, PaperStatus.STOPPED)

    def test_illegal_transition_rejected(self) -> None:
        state = paper_initial_state("paper-1")
        with self.assertRaises(PaperStateError):
            state.activate()
        with self.assertRaises(PaperStateError):
            state.transition(PaperStatus.ACTIVE)
        state = state.approve()
        with self.assertRaises(PaperStateError):
            state.break_circuit(reason="nope")
        state = state.stop()
        with self.assertRaises(PaperStateError):
            state.activate()

    def test_terminal_stop_never_resumed(self) -> None:
        state = paper_initial_state("paper-1").stop()
        self.assertEqual(state.status, PaperStatus.STOPPED)
        with self.assertRaises(PaperStateError):
            state.transition(PaperStatus.ACTIVE)

    def test_circuit_breaker_lifecycle(self) -> None:
        state = paper_initial_state("paper-1")
        state = state.approve().activate()
        state = state.break_circuit(reason="drawdown 10%", recorded_at=_utc_at(2026, 2, 1, 9))
        self.assertEqual(state.status, PaperStatus.CIRCUIT_BROKEN)
        self.assertEqual(state.diagnostics.reason, "drawdown 10%")
        state = state.recover(recorded_at=_utc_at(2026, 2, 2, 9))
        self.assertEqual(state.status, PaperStatus.ACTIVE)

    def test_break_circuit_requires_reason(self) -> None:
        state = paper_initial_state("paper-1").approve().activate()
        with self.assertRaises(TypeError):
            state.break_circuit()

    def test_stop_records_reason(self) -> None:
        state = paper_initial_state("paper-1")
        state = state.stop(reason="stop condition reached")
        self.assertEqual(state.diagnostics.reason, "stop condition reached")

    def test_warnings_accumulate(self) -> None:
        state = paper_initial_state("paper-1")
        state = state.with_warning("first").with_warning("second")
        self.assertEqual(state.diagnostics.warnings, ("first", "second"))
        self.assertIn("warning: first", state.readable())

    def test_empty_run_id_rejected(self) -> None:
        with self.assertRaises(PaperStateError):
            PaperRunState(run_id="")


class AuditTrailTests(unittest.TestCase):
    """The self-validating UTC audit trail (SP 4.10)."""

    def test_transition_records_reason_and_timestamp(self) -> None:
        state = paper_initial_state("paper-1")
        state = state.approve(reason="reviewed", recorded_at=_utc_at(2026, 1, 1, 8))
        entry = state.transitions[0]
        self.assertEqual(entry.from_status, PaperStatus.DRAFT)
        self.assertEqual(entry.to_status, PaperStatus.APPROVED)
        self.assertEqual(entry.reason, "reviewed")
        self.assertIn("reviewed", entry.readable())

    def test_default_timestamp_is_utc_now(self) -> None:
        state = paper_initial_state("paper-1").approve()
        entry = state.transitions[0]
        self.assertIsNotNone(entry.recorded_at.utcoffset())
        self.assertEqual(entry.recorded_at.utcoffset().total_seconds(), 0.0)

    def test_naive_or_offset_timestamp_rejected(self) -> None:
        state = paper_initial_state("paper-1")
        with self.assertRaises(ValueError):
            state.approve(recorded_at=datetime(2026, 1, 1, 8))
        with self.assertRaises(ValueError):
            state.approve(recorded_at=_offset_at(8))

    def test_broken_chain_rejected(self) -> None:
        first = PaperTransition(PaperStatus.DRAFT, PaperStatus.APPROVED, _utc_at(2026, 1, 1, 8))
        # second entry does not follow the first entry's target
        second = PaperTransition(PaperStatus.ACTIVE, PaperStatus.PAUSED, _utc_at(2026, 1, 2, 8))
        with self.assertRaises(PaperStateError):
            PaperRunState(
                run_id="paper-1",
                status=PaperStatus.PAUSED,
                transitions=(first, second),
            )

    def test_mismatched_final_transition_rejected(self) -> None:
        first = PaperTransition(PaperStatus.DRAFT, PaperStatus.APPROVED, _utc_at(2026, 1, 1, 8))
        with self.assertRaises(PaperStateError):
            PaperRunState(
                run_id="paper-1",
                status=PaperStatus.ACTIVE,
                transitions=(first,),
            )

    def test_readable_renders_trail(self) -> None:
        state = paper_initial_state("paper-1").approve().activate()
        rendered = state.readable()
        self.assertIn("status ACTIVE", rendered)
        self.assertIn("DRAFT -> APPROVED", rendered)
        self.assertIn("APPROVED -> ACTIVE", rendered)


class DiagnosticsTests(unittest.TestCase):
    """The paper diagnostics record (SP 4.10)."""

    def test_defaults(self) -> None:
        self.assertEqual(PaperDiagnostics().warnings, ())
        self.assertIsNone(PaperDiagnostics().reason)

    def test_readable(self) -> None:
        diagnostics = PaperDiagnostics(warnings=("low coverage",), reason="drawdown 10%")
        rendered = diagnostics.readable()
        self.assertIn("reason: drawdown 10%", rendered)
        self.assertIn("warning: low coverage", rendered)
