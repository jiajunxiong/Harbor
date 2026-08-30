"""Risk approval suite (MVP 4 / SP 4.43).

Integration tests over SP 4.31-4.42 covering the acceptance dimensions:
超集中度 (over-concentration), 超单笔风险 (over per-trade risk), 低现金 (low
cash) and 审批拒绝路径 (approval-rejection paths) — all surfaced through the
order risk gate with the full rejection reason preserved.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.paper_approval import ApprovalScope, ApprovalWorkflow, RiskLevel
from harbor.core.paper_config import PaperRiskConfig
from harbor.core.paper_domain import ApprovalDecision, PaperOrder, PaperOrderStatus, PaperPriceType
from harbor.core.paper_risk_checks import (
    DailyRiskContext,
    DailyRiskInput,
    RiskFindingKind,
    run_daily_risk_check,
)
from harbor.core.paper_risk_gate import gate_order


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _order(symbol: str = "0001.HK") -> PaperOrder:
    return PaperOrder(
        order_id="order-1",
        paper_run_id="paper-1",
        market=Market.HK,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=100.0,
        currency=Currency.HKD,
        price_type=PaperPriceType.REFERENCE,
        status=PaperOrderStatus.CREATED,
        created_at=_utc_at(2026, 1, 2, 9),
        intention_id="sig-1",
    )


def _context(**overrides: object) -> DailyRiskContext:
    fields: dict[str, object] = {
        "portfolio_value": 1_000_000.0,
        "available_cash": 1_000_000.0,
        "risk": PaperRiskConfig(),
    }
    fields.update(overrides)
    return DailyRiskContext(**fields)  # type: ignore[arg-type]


class RiskApprovalSuiteTests(unittest.TestCase):
    """The acceptance paths through the gate (SP 4.43)."""

    def test_over_concentration_rejected(self) -> None:
        # position 60k + buy 50k = 110k = 11% > 5% single-stock cap
        report = run_daily_risk_check(
            as_of=date(2026, 1, 2),
            inputs=[
                DailyRiskInput(
                    market=Market.HK,
                    symbol="0001.HK",
                    trade_notional_base=50_000.0,
                    position_value_base=60_000.0,
                    industry="banks",
                )
            ],
            context=_context(),
        )
        outcome = gate_order(order=_order(), daily_report=report)
        self.assertFalse(outcome.allowed)
        self.assertTrue(
            any(RiskFindingKind.CONCENTRATION_SINGLE_STOCK.value in r for r in outcome.reasons)
        )

    def test_over_per_trade_risk_rejected(self) -> None:
        report = run_daily_risk_check(
            as_of=date(2026, 1, 2),
            inputs=[
                DailyRiskInput(
                    market=Market.HK,
                    symbol="0001.HK",
                    trade_notional_base=20_000.0,  # 2% > 0.5%
                    position_value_base=0.0,
                )
            ],
            context=_context(),
        )
        outcome = gate_order(order=_order(), daily_report=report)
        self.assertFalse(outcome.allowed)
        self.assertTrue(any(RiskFindingKind.TRADE_RISK.value in r for r in outcome.reasons))

    def test_low_cash_rejected(self) -> None:
        report = run_daily_risk_check(
            as_of=date(2026, 1, 2),
            inputs=[
                DailyRiskInput(
                    market=Market.HK,
                    symbol="0001.HK",
                    trade_notional_base=2_000_000.0,
                    position_value_base=0.0,
                )
            ],
            context=_context(available_cash=500_000.0),
        )
        outcome = gate_order(order=_order(), daily_report=report)
        self.assertFalse(outcome.allowed)
        self.assertTrue(any(RiskFindingKind.CASH.value in r for r in outcome.reasons))

    def test_approval_rejection_path(self) -> None:
        workflow = (
            ApprovalWorkflow()
            .request(
                request_id="req-1",
                paper_run_id="paper-1",
                scope=ApprovalScope.ORDER,
                scope_id="order-1",
                risk_level=RiskLevel.HIGH,
                reason="high risk",
                requested_at=_utc_at(2026, 1, 2, 9),
            )
            .decide(
                request_id="req-1",
                decision=ApprovalDecision.REJECTED,
                approver="alice",
                reason="too concentrated",
                decided_at=_utc_at(2026, 1, 2, 10),
            )
        )
        outcome = gate_order(order=_order(), approval=workflow, request_id="req-1")
        self.assertFalse(outcome.allowed)
        self.assertTrue(outcome.requires_approval)

    def test_clean_order_passes(self) -> None:
        report = run_daily_risk_check(
            as_of=date(2026, 1, 2),
            inputs=[
                DailyRiskInput(
                    market=Market.HK,
                    symbol="0001.HK",
                    trade_notional_base=1_000.0,
                    position_value_base=5_000.0,
                )
            ],
            context=_context(),
        )
        outcome = gate_order(order=_order(), daily_report=report)
        self.assertTrue(outcome.allowed)
