"""Three-tier drawdown suite (MVP 4 / SP 4.46).

Verifies the staged 5% / 8% / 10% triggers and the run state-machine
transitions into and out of ``CIRCUIT_BROKEN``.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Currency, NetValue
from harbor.core.paper_domain import PaperStatus
from harbor.core.paper_drawdown import DrawdownTier, evaluate_drawdown
from harbor.core.paper_recovery import RecoveryReview, recover_run
from harbor.core.paper_state_machine import paper_initial_state


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _series(*totals: float) -> list[NetValue]:
    return [
        NetValue(
            as_of_date=date(2026, 1, index + 1),
            currency=Currency.HKD,
            cash=total,
            securities_value=0.0,
        )
        for index, total in enumerate(totals)
    ]


class DrawdownTiersSuiteTests(unittest.TestCase):
    """The staged tiers (SP 4.46)."""

    def test_tiers_escalate_with_depth(self) -> None:
        self.assertEqual(
            evaluate_drawdown(net_values=_series(1_000_000.0, 950_000.0)).tier,
            DrawdownTier.WARN,
        )
        self.assertEqual(
            evaluate_drawdown(net_values=_series(1_000_000.0, 920_000.0)).tier,
            DrawdownTier.DEFEND,
        )
        self.assertEqual(
            evaluate_drawdown(net_values=_series(1_000_000.0, 900_000.0)).tier,
            DrawdownTier.CIRCUIT,
        )

    def test_circuit_tier_enters_circuit_broken_state(self) -> None:
        assessment = evaluate_drawdown(net_values=_series(1_000_000.0, 900_000.0))
        self.assertEqual(assessment.tier, DrawdownTier.CIRCUIT)
        state = paper_initial_state("paper-1")
        state = state.approve(recorded_at=_utc_at(2026, 1, 1, 9)).activate(
            recorded_at=_utc_at(2026, 1, 1, 10)
        )
        state = state.break_circuit(
            reason=assessment.alert or "circuit", recorded_at=_utc_at(2026, 1, 2, 9)
        )
        self.assertEqual(state.status, PaperStatus.CIRCUIT_BROKEN)

    def test_recovery_returns_to_active(self) -> None:
        from harbor.core.paper_domain import ApprovalDecision, RiskApproval

        state = paper_initial_state("paper-1")
        state = state.approve().activate().break_circuit(reason="drawdown 10%")
        review = RecoveryReview(
            paper_run_id="paper-1",
            reviewer="bob",
            reviewed_at=_utc_at(2026, 1, 5, 9),
            passed=True,
            notes="review passed",
        )
        approval = RiskApproval(
            approval_id="ap-1",
            paper_run_id="paper-1",
            scope="run:paper-1",
            approver="carol",
            decision=ApprovalDecision.APPROVED,
            rule="recovery_approval",
            reason="approved",
            decided_at=_utc_at(2026, 1, 5, 10),
        )
        recover_run(review=review, approval=approval)
        state = state.recover(recorded_at=_utc_at(2026, 1, 5, 11))
        self.assertEqual(state.status, PaperStatus.ACTIVE)

    def test_warn_tier_stops_adding_risk(self) -> None:
        assessment = evaluate_drawdown(net_values=_series(1_000_000.0, 950_000.0))
        self.assertIn("stop adding risk positions", assessment.actions)
