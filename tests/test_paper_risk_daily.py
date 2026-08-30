"""Daily risk check tests (MVP 4 / SP 4.32).

Verifies the pre-open daily risk rules: per-trade risk (单笔 ≤ 0.5%), available
cash, single-stock concentration (单股 ≤ 5%) and single-industry concentration
(单行业 ≤ 20%); violating orders are rejected with a recorded reason.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.paper_config import PaperRiskConfig
from harbor.core.paper_risk_checks import (
    DailyRiskContext,
    DailyRiskInput,
    RiskFindingKind,
    RiskFindingSeverity,
    run_daily_risk_check,
)

_AS_OF = date(2026, 1, 2)


def _context(**overrides: object) -> DailyRiskContext:
    fields: dict[str, object] = {
        "portfolio_value": 1_000_000.0,
        "available_cash": 1_000_000.0,
        "risk": PaperRiskConfig(),
    }
    fields.update(overrides)
    return DailyRiskContext(**fields)  # type: ignore[arg-type]


def _input(**overrides: object) -> DailyRiskInput:
    fields: dict[str, object] = {
        "market": Market.HK,
        "symbol": "0001.HK",
        "trade_notional_base": 1_000.0,
        "position_value_base": 10_000.0,
        "industry": "banks",
    }
    fields.update(overrides)
    return DailyRiskInput(**fields)  # type: ignore[arg-type]


class DailyRiskCheckTests(unittest.TestCase):
    """The daily risk rules (SP 4.32)."""

    def test_all_within_limits_approved(self) -> None:
        report = run_daily_risk_check(as_of=_AS_OF, inputs=[_input()], context=_context())
        self.assertTrue(report.approved)
        self.assertEqual(report.findings, ())

    def test_per_trade_risk_over_limit(self) -> None:
        report = run_daily_risk_check(
            as_of=_AS_OF,
            inputs=[_input(trade_notional_base=10_000.0)],  # 1% > 0.5%
            context=_context(),
        )
        self.assertFalse(report.approved)
        finding = report.findings[0]
        self.assertEqual(finding.kind, RiskFindingKind.TRADE_RISK)
        self.assertEqual(finding.severity, RiskFindingSeverity.ERROR)
        self.assertIn("per-trade risk", finding.reason)

    def test_cash_shortfall(self) -> None:
        report = run_daily_risk_check(
            as_of=_AS_OF,
            inputs=[_input(trade_notional_base=2_000_000.0)],
            context=_context(),
        )
        self.assertFalse(report.approved)
        kinds = {finding.kind for finding in report.findings}
        self.assertIn(RiskFindingKind.CASH, kinds)

    def test_single_stock_concentration_over_limit(self) -> None:
        report = run_daily_risk_check(
            as_of=_AS_OF,
            inputs=[_input(position_value_base=50_000.0, trade_notional_base=10_000.0)],
            context=_context(),
        )
        # (50k + 10k)/1M = 6% > 5%
        kinds = {finding.kind for finding in report.findings}
        self.assertIn(RiskFindingKind.CONCENTRATION_SINGLE_STOCK, kinds)
        self.assertIn("6.00%", report.findings[0].reason)

    def test_industry_concentration_over_limit(self) -> None:
        inputs = [
            _input(symbol="0001.HK", position_value_base=100_000.0),
            _input(symbol="0002.HK", position_value_base=100_000.0),
        ]
        report = run_daily_risk_check(as_of=_AS_OF, inputs=inputs, context=_context())
        # industry banks = 200k + 2*1k notional = 202k = 20.2% > 20%
        industry_finding = next(
            finding
            for finding in report.findings
            if finding.kind is RiskFindingKind.CONCENTRATION_INDUSTRY
        )
        self.assertIn("industry banks", industry_finding.reason)

    def test_findings_for_symbol(self) -> None:
        report = run_daily_risk_check(
            as_of=_AS_OF,
            inputs=[_input(symbol="0001.HK", trade_notional_base=10_000.0)],
            context=_context(),
        )
        self.assertEqual(len(report.findings_for("0001.HK")), 1)
        self.assertEqual(report.findings_for("0002.HK"), ())

    def test_readable(self) -> None:
        report = run_daily_risk_check(
            as_of=_AS_OF,
            inputs=[_input(trade_notional_base=10_000.0)],
            context=_context(),
        )
        rendered = report.readable()
        self.assertIn("daily risk check", rendered)
        self.assertIn("BLOCKED", rendered)

    def test_invalid_context_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _context(portfolio_value=0.0)
        with self.assertRaises(ValueError):
            run_daily_risk_check(
                as_of=_AS_OF,
                inputs=[_input()],
                context=_context(available_cash=-1.0),
            )
