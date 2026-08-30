"""Concentration boundary suite (MVP 4 / SP 4.47).

Verifies the single-stock, single-industry, market and currency exposure
boundaries — at the limit passes, just over the limit is rejected.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Currency, Market
from harbor.core.paper_config import PaperRiskConfig
from harbor.core.paper_risk_checks import (
    DailyRiskContext,
    DailyRiskInput,
    MonthlyRiskInput,
    MonthlyRiskLimits,
    RiskFindingKind,
    run_daily_risk_check,
    run_monthly_risk_check,
)

_AS_OF = date(2026, 1, 2)


class SingleStockBoundaryTests(unittest.TestCase):
    """The single-stock cap (单股, SP 4.47)."""

    def test_at_limit_passes(self) -> None:
        # (45k position + 5k buy) / 1M = 5% exactly
        report = run_daily_risk_check(
            as_of=_AS_OF,
            inputs=[
                DailyRiskInput(
                    market=Market.HK,
                    symbol="0001.HK",
                    trade_notional_base=5_000.0,
                    position_value_base=45_000.0,
                )
            ],
            context=DailyRiskContext(
                portfolio_value=1_000_000.0,
                available_cash=1_000_000.0,
                risk=PaperRiskConfig(),
            ),
        )
        self.assertTrue(report.approved)

    def test_just_over_limit_rejected(self) -> None:
        # (45k + 5.5k) / 1M = 5.05% > 5%
        report = run_daily_risk_check(
            as_of=_AS_OF,
            inputs=[
                DailyRiskInput(
                    market=Market.HK,
                    symbol="0001.HK",
                    trade_notional_base=5_500.0,
                    position_value_base=45_000.0,
                )
            ],
            context=DailyRiskContext(
                portfolio_value=1_000_000.0,
                available_cash=1_000_000.0,
                risk=PaperRiskConfig(),
            ),
        )
        kinds = {finding.kind for finding in report.findings}
        self.assertIn(RiskFindingKind.CONCENTRATION_SINGLE_STOCK, kinds)


class IndustryBoundaryTests(unittest.TestCase):
    """The single-industry cap (单行业, SP 4.47)."""

    @staticmethod
    def _industry_inputs(position_value_base: float) -> list[DailyRiskInput]:
        return [
            DailyRiskInput(
                market=Market.HK,
                symbol=f"000{i}.HK",
                trade_notional_base=0.0,
                position_value_base=position_value_base,
                industry="banks",
            )
            for i in range(1, 5)
        ]

    def test_industry_at_limit_passes(self) -> None:
        # 4 symbols x 50k = 200k industry = 20% exactly; each symbol 5% at the cap
        report = run_daily_risk_check(
            as_of=_AS_OF,
            inputs=self._industry_inputs(50_000.0),
            context=DailyRiskContext(
                portfolio_value=1_000_000.0,
                available_cash=1_000_000.0,
                risk=PaperRiskConfig(),
            ),
        )
        self.assertTrue(report.approved)

    def test_industry_over_limit_rejected(self) -> None:
        report = run_daily_risk_check(
            as_of=_AS_OF,
            inputs=self._industry_inputs(51_000.0),
            context=DailyRiskContext(
                portfolio_value=1_000_000.0,
                available_cash=1_000_000.0,
                risk=PaperRiskConfig(),
            ),
        )
        kinds = {finding.kind for finding in report.findings}
        self.assertIn(RiskFindingKind.CONCENTRATION_INDUSTRY, kinds)


class MarketCurrencyBoundaryTests(unittest.TestCase):
    """The market and currency exposure limits (SP 4.47)."""

    def test_market_at_limit_passes(self) -> None:
        report = run_monthly_risk_check(
            as_of=_AS_OF,
            inputs=[
                MonthlyRiskInput(
                    market=Market.US,
                    symbol="AAPL",
                    position_value_base=500_000.0,
                    currency=Currency.USD,
                )
            ],
            portfolio_value=1_000_000.0,
            cumulative_loss_pct=0.0,
            risk=PaperRiskConfig(),
            limits=MonthlyRiskLimits(max_market_pct=0.5),
        )
        self.assertEqual(report.findings, ())

    def test_market_over_limit_rejected(self) -> None:
        report = run_monthly_risk_check(
            as_of=_AS_OF,
            inputs=[
                MonthlyRiskInput(
                    market=Market.US,
                    symbol="AAPL",
                    position_value_base=510_000.0,
                    currency=Currency.USD,
                )
            ],
            portfolio_value=1_000_000.0,
            cumulative_loss_pct=0.0,
            risk=PaperRiskConfig(),
            limits=MonthlyRiskLimits(max_market_pct=0.5),
        )
        kinds = {finding.kind for finding in report.findings}
        self.assertIn(RiskFindingKind.EXPOSURE_MARKET, kinds)

    def test_currency_over_limit_rejected(self) -> None:
        report = run_monthly_risk_check(
            as_of=_AS_OF,
            inputs=[
                MonthlyRiskInput(
                    market=Market.US,
                    symbol="AAPL",
                    position_value_base=510_000.0,
                    currency=Currency.USD,
                )
            ],
            portfolio_value=1_000_000.0,
            cumulative_loss_pct=0.0,
            risk=PaperRiskConfig(),
            limits=MonthlyRiskLimits(max_currency_pct=0.5),
        )
        kinds = {finding.kind for finding in report.findings}
        self.assertIn(RiskFindingKind.EXPOSURE_CURRENCY, kinds)
