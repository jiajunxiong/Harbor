"""Monthly risk check tests (MVP 4 / SP 4.33).

Verifies the monthly evaluation of market / industry / currency exposure and
cumulative metrics; over-limit dimensions trigger limit findings and alerts.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Currency, Market
from harbor.core.paper_config import PaperRiskConfig
from harbor.core.paper_risk_checks import (
    MonthlyRiskInput,
    MonthlyRiskLimits,
    RiskFindingKind,
    RiskFindingSeverity,
    run_monthly_risk_check,
)

_AS_OF = date(2026, 1, 31)


def _input(**overrides: object) -> MonthlyRiskInput:
    fields: dict[str, object] = {
        "market": Market.HK,
        "symbol": "0001.HK",
        "position_value_base": 100_000.0,
        "currency": Currency.HKD,
        "industry": "banks",
    }
    fields.update(overrides)
    return MonthlyRiskInput(**fields)  # type: ignore[arg-type]


def _check(**overrides: object):
    fields: dict[str, object] = {
        "as_of": _AS_OF,
        "inputs": [_input()],
        "portfolio_value": 1_000_000.0,
        "cumulative_loss_pct": 0.02,
        "risk": PaperRiskConfig(),
        "limits": None,
    }
    fields.update(overrides)
    return run_monthly_risk_check(**fields)  # type: ignore[arg-type]


class MonthlyRiskCheckTests(unittest.TestCase):
    """The monthly exposure rules (SP 4.33)."""

    def test_all_within_limits(self) -> None:
        report = _check()
        self.assertEqual(report.findings, ())
        self.assertIn(("market:HK", 0.10), report.exposures)
        self.assertIn(("currency:HKD", 0.10), report.exposures)
        self.assertIn(("industry:banks", 0.10), report.exposures)
        self.assertIn(("cumulative_loss", 0.02), report.exposures)

    def test_market_exposure_over_limit(self) -> None:
        limits = MonthlyRiskLimits(max_market_pct=0.5)
        report = _check(inputs=[_input(position_value_base=600_000.0)], limits=limits)
        kinds = {finding.kind for finding in report.findings}
        self.assertIn(RiskFindingKind.EXPOSURE_MARKET, kinds)
        self.assertIn("60.00%", report.findings[0].reason)

    def test_currency_exposure_over_limit(self) -> None:
        limits = MonthlyRiskLimits(max_currency_pct=0.5)
        report = _check(inputs=[_input(position_value_base=600_000.0)], limits=limits)
        kinds = {finding.kind for finding in report.findings}
        self.assertIn(RiskFindingKind.EXPOSURE_CURRENCY, kinds)

    def test_industry_exposure_over_limit(self) -> None:
        # industry banks 60% > risk.max_industry_pct 20%
        report = _check(inputs=[_input(position_value_base=600_000.0)])
        kinds = {finding.kind for finding in report.findings}
        self.assertIn(RiskFindingKind.EXPOSURE_INDUSTRY, kinds)

    def test_cumulative_loss_over_limit(self) -> None:
        report = _check(cumulative_loss_pct=0.15)
        kinds = {finding.kind for finding in report.findings}
        self.assertIn(RiskFindingKind.CUMULATIVE_LOSS, kinds)
        self.assertIn("15.00%", report.findings[0].reason)

    def test_severity_error_and_exceeded_limits(self) -> None:
        limits = MonthlyRiskLimits(max_market_pct=0.5)
        report = _check(inputs=[_input(position_value_base=600_000.0)], limits=limits)
        self.assertTrue(all(f.severity is RiskFindingSeverity.ERROR for f in report.findings))
        self.assertIn("market:HK", report.exceeded_limits)

    def test_readable(self) -> None:
        report = _check(cumulative_loss_pct=0.15)
        rendered = report.readable()
        self.assertIn("monthly risk check", rendered)
        self.assertIn("CUMULATIVE_LOSS", rendered)

    def test_invalid_portfolio_value(self) -> None:
        with self.assertRaises(ValueError):
            _check(portfolio_value=0.0)

    def test_limits_validated(self) -> None:
        with self.assertRaises(ValueError):
            MonthlyRiskLimits(max_market_pct=0.0)
        with self.assertRaises(ValueError):
            MonthlyRiskLimits(cumulative_loss_limit_pct=1.5)
