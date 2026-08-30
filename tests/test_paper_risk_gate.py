"""Order risk gate tests (MVP 4 / SP 4.41).

Verifies that an order passes the gate only when the run is not frozen, the
daily risk report has no blocking finding, and any high-risk approval is
approved — with the complete rejection reason preserved.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.paper_approval import ApprovalScope, ApprovalWorkflow, RiskLevel
from harbor.core.paper_domain import ApprovalDecision, PaperOrder, PaperOrderStatus, PaperPriceType
from harbor.core.paper_freeze import freeze_run
from harbor.core.paper_risk_checks import (
    DailyRiskReport,
    RiskFinding,
    RiskFindingKind,
    RiskFindingSeverity,
)
from harbor.core.paper_risk_gate import (
    PaperRiskGateError,
    RiskGateOutcome,
    gate_order,
)


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
        created_at=_utc_at(2026, 1, 2, 9),
        intention_id="sig-1",
    )


def _blocked_report() -> DailyRiskReport:
    return DailyRiskReport(
        as_of=date(2026, 1, 2),
        findings=(
            RiskFinding(
                kind=RiskFindingKind.TRADE_RISK,
                rule="max_position_pct",
                reason="per-trade risk 1.00% exceeds 0.50%.",
                severity=RiskFindingSeverity.ERROR,
                market=Market.HK,
                symbol="0001.HK",
            ),
        ),
    )


def _approval(decision: ApprovalDecision | None) -> ApprovalWorkflow:
    workflow = ApprovalWorkflow().request(
        request_id="req-1",
        paper_run_id="paper-1",
        scope=ApprovalScope.ORDER,
        scope_id="order-1",
        risk_level=RiskLevel.HIGH,
        reason="order exceeds risk limit",
        requested_at=_utc_at(2026, 1, 2, 9),
    )
    if decision is not None:
        workflow = workflow.decide(
            request_id="req-1",
            decision=decision,
            approver="alice",
            reason="reviewed",
            decided_at=_utc_at(2026, 1, 2, 10),
        )
    return workflow


class GateOrderTests(unittest.TestCase):
    """The risk gate (SP 4.41)."""

    def test_no_blockers_allowed(self) -> None:
        outcome = gate_order(order=_order())
        self.assertIsInstance(outcome, RiskGateOutcome)
        self.assertTrue(outcome.allowed)
        self.assertEqual(outcome.reasons, ())

    def test_freeze_rejects(self) -> None:
        freeze = freeze_run(
            paper_run_id="paper-1",
            reason="drawdown exceeded 10%",
            frozen_at=_utc_at(2026, 1, 2, 9),
        )
        outcome = gate_order(order=_order(), freeze=freeze)
        self.assertFalse(outcome.allowed)
        self.assertIn("is frozen", outcome.reasons[0])

    def test_risk_finding_rejects_with_reason(self) -> None:
        outcome = gate_order(order=_order(), daily_report=_blocked_report())
        self.assertFalse(outcome.allowed)
        self.assertIn("per-trade risk 1.00%", outcome.reasons[0])

    def test_high_risk_requires_approval(self) -> None:
        outcome = gate_order(order=_order(), approval=_approval(None), request_id="req-1")
        self.assertFalse(outcome.allowed)
        self.assertTrue(outcome.requires_approval)
        self.assertIn("requires human approval", outcome.reasons[0])

    def test_approved_high_risk_passes(self) -> None:
        outcome = gate_order(
            order=_order(),
            approval=_approval(ApprovalDecision.APPROVED),
            request_id="req-1",
        )
        self.assertTrue(outcome.allowed)
        self.assertTrue(outcome.requires_approval)

    def test_rejected_high_risk_blocked(self) -> None:
        outcome = gate_order(
            order=_order(),
            approval=_approval(ApprovalDecision.REJECTED),
            request_id="req-1",
        )
        self.assertFalse(outcome.allowed)

    def test_request_id_requires_workflow(self) -> None:
        with self.assertRaises(PaperRiskGateError):
            gate_order(order=_order(), request_id="req-1")

    def test_readable(self) -> None:
        outcome = gate_order(
            order=_order(),
            freeze=freeze_run(paper_run_id="paper-1", reason="x", frozen_at=_utc_at(2026, 1, 2, 9)),
        )
        self.assertIn("risk gate order-1: REJECTED", outcome.readable())
