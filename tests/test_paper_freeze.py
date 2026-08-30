"""Freeze state tests (MVP 4 / SP 4.40).

Verifies that a frozen run rejects new orders and that both the freeze and its
recovery are recorded.
"""

import unittest
from datetime import datetime, timezone

from harbor.core.paper_freeze import (
    FreezeState,
    PaperFreezeError,
    freeze_run,
    recover_freeze,
    reject_during_freeze,
)


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


class FreezeTests(unittest.TestCase):
    """Freezing and recovering a run (SP 4.40)."""

    def test_freeze_blocks_new_orders(self) -> None:
        state = freeze_run(
            paper_run_id="paper-1",
            reason="daily drawdown exceeded 10%",
            frozen_at=_utc_at(2026, 1, 2, 9),
        )
        self.assertTrue(state.frozen)
        self.assertTrue(state.blocks_new_orders())
        reason = reject_during_freeze(state)
        self.assertIsNotNone(reason)
        self.assertIn("is frozen", reason)
        self.assertIn("daily drawdown", reason)

    def test_recover_unfreezes(self) -> None:
        state = freeze_run(
            paper_run_id="paper-1",
            reason="monthly loss limit",
            frozen_at=_utc_at(2026, 1, 2, 9),
        )
        recovered = recover_freeze(state=state, recovered_at=_utc_at(2026, 1, 5, 9))
        self.assertFalse(recovered.frozen)
        self.assertFalse(recovered.blocks_new_orders())
        self.assertIsNotNone(recovered.recovered_at)
        self.assertIsNone(reject_during_freeze(recovered))

    def test_scope_without_new_orders_does_not_block(self) -> None:
        state = freeze_run(
            paper_run_id="paper-1",
            scope="parameter_changes",
            reason="parameter freeze",
            frozen_at=_utc_at(2026, 1, 2, 9),
        )
        self.assertTrue(state.frozen)
        self.assertFalse(state.blocks_new_orders())
        self.assertIsNone(reject_during_freeze(state))

    def test_recover_non_frozen_rejected(self) -> None:
        state = FreezeState(
            paper_run_id="paper-1",
            frozen=False,
            scope="new_orders",
            reason="x",
            frozen_at=_utc_at(2026, 1, 2, 9),
            recovered_at=_utc_at(2026, 1, 5, 9),
        )
        with self.assertRaises(PaperFreezeError):
            recover_freeze(state=state)

    def test_recover_before_frozen_at_rejected(self) -> None:
        state = freeze_run(
            paper_run_id="paper-1",
            reason="x",
            frozen_at=_utc_at(2026, 1, 2, 9),
        )
        with self.assertRaises(PaperFreezeError):
            recover_freeze(state=state, recovered_at=_utc_at(2026, 1, 1, 9))

    def test_invalid_state(self) -> None:
        with self.assertRaises(PaperFreezeError):
            freeze_run(paper_run_id="", reason="x", frozen_at=_utc_at(2026, 1, 2, 9))
        with self.assertRaises(ValueError):
            freeze_run(paper_run_id="p", reason="x", frozen_at=datetime(2026, 1, 2, 9))
        with self.assertRaises(PaperFreezeError):
            FreezeState(
                paper_run_id="p",
                frozen=True,
                scope="new_orders",
                reason="x",
                frozen_at=_utc_at(2026, 1, 2, 9),
                recovered_at=_utc_at(2026, 1, 3, 9),
            )
        with self.assertRaises(PaperFreezeError):
            FreezeState(
                paper_run_id="p",
                frozen=False,
                scope="new_orders",
                reason="x",
                frozen_at=_utc_at(2026, 1, 2, 9),
            )

    def test_readable(self) -> None:
        state = freeze_run(paper_run_id="paper-1", reason="x", frozen_at=_utc_at(2026, 1, 2, 9))
        self.assertIn("paper run paper-1 FROZEN", state.readable())
