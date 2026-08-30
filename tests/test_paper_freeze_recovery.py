"""Freeze / recovery suite (MVP 4 / SP 4.49).

Verifies that new orders are rejected during a freeze, that the freeze
persists while the recovery condition is not met, and that a met recovery
condition unfreezes the run.
"""

import unittest
from datetime import datetime, timezone

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.paper_domain import (
    ApprovalDecision,
    PaperOrder,
    PaperOrderStatus,
    PaperPriceType,
    RiskApproval,
)
from harbor.core.paper_freeze import freeze_run, recover_freeze
from harbor.core.paper_recovery import RecoveryReview, recover_run
from harbor.core.paper_risk_gate import gate_order


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _order() -> PaperOrder:
    return PaperOrder(
        order_id="order-1",
        paper_run_id="paper-1",
        market=Market.HK,
        symbol="0001.HK",
        side=OrderSide.BUY,
        quantity=100.0,
        currency=Currency.HKD,
        price_type=PaperPriceType.REFERENCE,
        status=PaperOrderStatus.CREATED,
        created_at=_utc_at(2026, 1, 3, 9),
        intention_id="sig-1",
    )


def _approval() -> RiskApproval:
    return RiskApproval(
        approval_id="ap-1",
        paper_run_id="paper-1",
        scope="run:paper-1",
        approver="carol",
        decision=ApprovalDecision.APPROVED,
        rule="recovery_approval",
        reason="approved",
        decided_at=_utc_at(2026, 1, 5, 10),
    )


class FreezeRecoverySuiteTests(unittest.TestCase):
    """The freeze / recovery persistence (SP 4.49)."""

    def test_freeze_rejects_orders(self) -> None:
        freeze = freeze_run(
            paper_run_id="paper-1",
            reason="drawdown exceeded 10%",
            frozen_at=_utc_at(2026, 1, 3, 9),
        )
        outcome = gate_order(order=_order(), freeze=freeze)
        self.assertFalse(outcome.allowed)
        self.assertIn("is frozen", outcome.reasons[0])

    def test_freeze_persists_when_recovery_not_met(self) -> None:
        freeze = freeze_run(
            paper_run_id="paper-1",
            reason="drawdown exceeded 10%",
            frozen_at=_utc_at(2026, 1, 3, 9),
        )
        # recovery condition not met: the independent review failed
        failed_review = RecoveryReview(
            paper_run_id="paper-1",
            reviewer="bob",
            reviewed_at=_utc_at(2026, 1, 5, 9),
            passed=False,
            notes="review did not pass",
        )
        from harbor.core.paper_recovery import recover_run

        with self.assertRaises(Exception):
            recover_run(review=failed_review, approval=_approval())
        # the run stays frozen and keeps rejecting new orders
        outcome = gate_order(order=_order(), freeze=freeze)
        self.assertFalse(outcome.allowed)

    def test_recovery_met_unfreezes(self) -> None:
        freeze = freeze_run(
            paper_run_id="paper-1",
            reason="drawdown exceeded 10%",
            frozen_at=_utc_at(2026, 1, 3, 9),
        )
        review = RecoveryReview(
            paper_run_id="paper-1",
            reviewer="bob",
            reviewed_at=_utc_at(2026, 1, 5, 9),
            passed=True,
            notes="review passed",
        )
        recover_run(review=review, approval=_approval())
        recovered = recover_freeze(state=freeze, recovered_at=_utc_at(2026, 1, 5, 11))
        self.assertFalse(recovered.blocks_new_orders())
        outcome = gate_order(order=_order(), freeze=recovered)
        self.assertTrue(outcome.allowed)

    def test_freeze_readable_records_reason(self) -> None:
        freeze = freeze_run(
            paper_run_id="paper-1",
            reason="daily loss limit",
            frozen_at=_utc_at(2026, 1, 3, 9),
        )
        self.assertIn("daily loss limit", freeze.readable())
