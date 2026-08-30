"""Paper order state machine tests (MVP 4 / SP 4.20).

Verifies the legal order-lifecycle transitions (CREATED -> SUBMITTED ->
PARTIALLY_FILLED -> FILLED, with CANCELLED / REJECTED) and that illegal or
terminal transitions are rejected, with a self-validating UTC audit trail.
"""

import unittest
from datetime import datetime, timezone

from harbor.core.paper_domain import PaperOrderStatus
from harbor.core.paper_order_state_machine import (
    PaperOrderStateError,
    allowed_transitions,
    can_transition,
    paper_order_initial_state,
)


def _utc_at(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class TransitionRulesTests(unittest.TestCase):
    """The legal order-lifecycle table (SP 4.20)."""

    def test_created_allowed(self) -> None:
        self.assertEqual(
            allowed_transitions(PaperOrderStatus.CREATED),
            frozenset(
                {
                    PaperOrderStatus.SUBMITTED,
                    PaperOrderStatus.CANCELLED,
                    PaperOrderStatus.REJECTED,
                }
            ),
        )
        self.assertTrue(can_transition(PaperOrderStatus.CREATED, PaperOrderStatus.SUBMITTED))
        self.assertFalse(can_transition(PaperOrderStatus.CREATED, PaperOrderStatus.FILLED))

    def test_submitted_allowed(self) -> None:
        for target in (
            PaperOrderStatus.PARTIALLY_FILLED,
            PaperOrderStatus.FILLED,
            PaperOrderStatus.CANCELLED,
            PaperOrderStatus.REJECTED,
        ):
            self.assertTrue(can_transition(PaperOrderStatus.SUBMITTED, target))

    def test_partially_filled_allowed(self) -> None:
        self.assertTrue(can_transition(PaperOrderStatus.PARTIALLY_FILLED, PaperOrderStatus.FILLED))
        self.assertTrue(
            can_transition(PaperOrderStatus.PARTIALLY_FILLED, PaperOrderStatus.CANCELLED)
        )
        self.assertFalse(
            can_transition(PaperOrderStatus.PARTIALLY_FILLED, PaperOrderStatus.SUBMITTED)
        )

    def test_terminal_states(self) -> None:
        for status in (
            PaperOrderStatus.FILLED,
            PaperOrderStatus.CANCELLED,
            PaperOrderStatus.REJECTED,
        ):
            self.assertEqual(allowed_transitions(status), frozenset())


class PaperOrderStateTests(unittest.TestCase):
    """Advancing the order state with an audit trail (SP 4.20)."""

    def test_full_lifecycle(self) -> None:
        state = paper_order_initial_state("order-1")
        self.assertEqual(state.status, PaperOrderStatus.CREATED)
        state = state.submit(recorded_at=_utc_at(2026, 1, 2, 9))
        state = state.fill_partial(recorded_at=_utc_at(2026, 1, 2, 9, 30))
        state = state.complete(recorded_at=_utc_at(2026, 1, 2, 10))
        self.assertEqual(state.status, PaperOrderStatus.FILLED)
        self.assertEqual(len(state.transitions), 3)
        self.assertEqual(state.transitions[0].from_status, PaperOrderStatus.CREATED)
        self.assertEqual(state.transitions[-1].to_status, PaperOrderStatus.FILLED)

    def test_cancel_remainder_after_partial(self) -> None:
        state = paper_order_initial_state("order-1")
        state = state.submit().fill_partial().cancel(reason="remainder cancelled")
        self.assertEqual(state.status, PaperOrderStatus.CANCELLED)

    def test_reject_requires_reason(self) -> None:
        state = paper_order_initial_state("order-1")
        with self.assertRaises(TypeError):
            state.reject()
        state = state.reject(reason="insufficient cash")
        self.assertEqual(state.status, PaperOrderStatus.REJECTED)

    def test_illegal_transition_rejected(self) -> None:
        state = paper_order_initial_state("order-1")
        with self.assertRaises(PaperOrderStateError):
            state.complete()
        state = state.submit()
        with self.assertRaises(PaperOrderStateError):
            state.transition(PaperOrderStatus.CREATED)
        state = state.complete()
        with self.assertRaises(PaperOrderStateError):
            state.cancel()

    def test_empty_order_id_rejected(self) -> None:
        with self.assertRaises(PaperOrderStateError):
            paper_order_initial_state("")

    def test_naive_timestamp_rejected(self) -> None:
        state = paper_order_initial_state("order-1")
        with self.assertRaises(ValueError):
            state.submit(recorded_at=datetime(2026, 1, 2, 9))

    def test_default_timestamp_is_utc(self) -> None:
        state = paper_order_initial_state("order-1").submit()
        self.assertEqual(state.transitions[0].recorded_at.utcoffset().total_seconds(), 0.0)

    def test_readable(self) -> None:
        state = paper_order_initial_state("order-1").submit()
        rendered = state.readable()
        self.assertIn("paper order order-1", rendered)
        self.assertIn("CREATED -> SUBMITTED", rendered)
