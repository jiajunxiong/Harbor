"""Paper unfilled handling tests (MVP 4 / SP 4.21).

Verifies that partially-filled and unfilled orders keep their reason and are
cancelled or deferred per the configured policy, never silently dropped.
"""

import unittest

from harbor.core.backtest_config import UnfilledPolicy
from harbor.core.paper_unfilled import (
    PaperUnfilledError,
    decide_unfilled,
)


class DecideUnfilledTests(unittest.TestCase):
    """The unfilled outcome (SP 4.21)."""

    def test_full_fill(self) -> None:
        outcome = decide_unfilled(
            order_id="order-1",
            requested_quantity=100.0,
            filled_quantity=100.0,
            policy=UnfilledPolicy.CANCEL,
            reason="fully filled",
        )
        self.assertTrue(outcome.is_full)
        self.assertFalse(outcome.is_partial)
        self.assertEqual(outcome.unfilled_quantity, 0.0)

    def test_partial_fill_cancel(self) -> None:
        outcome = decide_unfilled(
            order_id="order-1",
            requested_quantity=100.0,
            filled_quantity=40.0,
            policy=UnfilledPolicy.CANCEL,
            reason="volume participation capped quantity to 40",
        )
        self.assertTrue(outcome.is_partial)
        self.assertEqual(outcome.unfilled_quantity, 60.0)
        self.assertEqual(outcome.cancelled_quantity, 60.0)
        self.assertEqual(outcome.deferred_quantity, 0.0)
        self.assertIn("volume participation", outcome.reason)

    def test_partial_fill_defer(self) -> None:
        outcome = decide_unfilled(
            order_id="order-1",
            requested_quantity=100.0,
            filled_quantity=40.0,
            policy=UnfilledPolicy.DEFER,
            reason="volume participation capped quantity to 40",
        )
        self.assertEqual(outcome.deferred_quantity, 60.0)
        self.assertEqual(outcome.cancelled_quantity, 0.0)

    def test_unfilled(self) -> None:
        outcome = decide_unfilled(
            order_id="order-1",
            requested_quantity=100.0,
            filled_quantity=0.0,
            policy=UnfilledPolicy.DEFER,
            reason="no liquidity on the day",
        )
        self.assertTrue(outcome.is_unfilled)
        self.assertEqual(outcome.unfilled_quantity, 100.0)
        self.assertEqual(outcome.deferred_quantity, 100.0)

    def test_reason_never_dropped(self) -> None:
        outcome = decide_unfilled(
            order_id="order-1",
            requested_quantity=100.0,
            filled_quantity=50.0,
            policy=UnfilledPolicy.CANCEL,
            reason="partially filled; remainder cancelled per config",
        )
        self.assertIn("partially filled", outcome.reason)
        self.assertIn("order-1", outcome.readable())

    def test_invalid_outcomes_rejected(self) -> None:
        with self.assertRaises(PaperUnfilledError):
            decide_unfilled(
                order_id="",
                requested_quantity=100.0,
                filled_quantity=100.0,
                policy=UnfilledPolicy.CANCEL,
                reason="x",
            )
        with self.assertRaises(PaperUnfilledError):
            decide_unfilled(
                order_id="o",
                requested_quantity=0.0,
                filled_quantity=0.0,
                policy=UnfilledPolicy.CANCEL,
                reason="x",
            )
        with self.assertRaises(PaperUnfilledError):
            decide_unfilled(
                order_id="o",
                requested_quantity=100.0,
                filled_quantity=101.0,
                policy=UnfilledPolicy.CANCEL,
                reason="x",
            )
        with self.assertRaises(PaperUnfilledError):
            decide_unfilled(
                order_id="o",
                requested_quantity=100.0,
                filled_quantity=50.0,
                policy=UnfilledPolicy.CANCEL,
                reason="",
            )
