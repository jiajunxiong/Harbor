"""Default parameter documentation tests (MVP 2 / SP 2.73).

Verifies that the defaults documented in ``docs/backtest_default_parameters.md``
match the actual code defaults (an anti-drift guard): if a default is changed in
code without updating the documentation, these tests fail, forcing the two to
stay in sync. Also verifies the document covers costs, slippage, liquidity, FX
and missing-factor handling, and states explicitly that the defaults are
research assumptions, not market facts.
"""

import unittest
from datetime import date
from pathlib import Path

from harbor.core.backtest_config import (
    BacktestConfig,
    BenchmarkConfig,
    BenchmarkKind,
    CostConfig,
    DividendConfig,
    FillConfig,
    FillRule,
    MarketQuota,
    RebalanceFrequency,
    RiskConfig,
    SuspensionConfig,
    SuspensionValuation,
    UnfilledPolicy,
    VolumeConfig,
)
from harbor.core.backtest_domain import Currency, Market
from harbor.core.candidate_filter import CandidateFilterConfig
from harbor.core.factor_scoring import FactorScoreConfig, MissingPolicy
from harbor.core.history_window import WindowConfig

_DOC_PATH = Path(__file__).resolve().parents[1] / "docs" / "backtest_default_parameters.md"


def _minimal_config() -> BacktestConfig:
    """A minimal valid config exercising every top-level default (SP 2.4)."""
    return BacktestConfig(
        markets=(Market.HK,),
        market_quotas=(MarketQuota(market=Market.HK, target_count=1, weight=1.0),),
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        base_currency=Currency.HKD,
    )


class CostDefaultsTests(unittest.TestCase):
    """Lock the documented cost defaults (SP 2.37 / 2.38)."""

    def setUp(self) -> None:
        self.cost = CostConfig()

    def test_commission(self) -> None:
        self.assertEqual(self.cost.commission_rate, 0.0005)
        self.assertEqual(self.cost.min_commission, 0.0)

    def test_hk_rates(self) -> None:
        self.assertEqual(self.cost.stamp_duty_rate, 0.001)
        self.assertEqual(self.cost.transaction_levy_rate, 0.000027)
        self.assertEqual(self.cost.trading_fee_rate, 0.0000565)

    def test_us_rate_and_slippage(self) -> None:
        self.assertEqual(self.cost.regulatory_fee_rate, 0.0000278)
        self.assertEqual(self.cost.slippage_bps, 0.0)

    def test_board_lot(self) -> None:
        self.assertEqual(self.cost.lot_size, 100)


class RiskAndExecutionDefaultsTests(unittest.TestCase):
    """Lock the documented risk / fill / volume / suspension / dividend defaults."""

    def test_risk_defaults(self) -> None:
        risk = RiskConfig()
        self.assertEqual(risk.max_position_pct, 0.2)
        self.assertEqual(risk.max_market_pct, 1.0)
        self.assertEqual(risk.min_cash_pct, 0.0)

    def test_fill_defaults(self) -> None:
        self.assertEqual(FillConfig().fill_rule, FillRule.CLOSE)

    def test_volume_defaults(self) -> None:
        volume = VolumeConfig()
        self.assertEqual(volume.participation_rate, 0.1)
        self.assertEqual(volume.on_unfilled, UnfilledPolicy.CANCEL)

    def test_suspension_defaults(self) -> None:
        suspension = SuspensionConfig()
        self.assertEqual(suspension.valuation, SuspensionValuation.LAST_PRICE)
        self.assertTrue(suspension.warn)

    def test_dividend_default(self) -> None:
        self.assertTrue(DividendConfig().include_special)

    def test_benchmark_default(self) -> None:
        self.assertEqual(BenchmarkConfig().kind, BenchmarkKind.CASH)

    def test_top_level_defaults(self) -> None:
        config = _minimal_config()
        self.assertEqual(config.strategy, "shareholder-return")
        self.assertEqual(config.strategy_version, "1.0.0")
        self.assertEqual(config.rebalance_frequency, RebalanceFrequency.QUARTERLY)
        self.assertEqual(config.initial_capital, 1_000_000.0)


class LiquidityAndMissingDataDefaultsTests(unittest.TestCase):
    """Lock the documented liquidity / FX / missing-factor handling defaults."""

    def test_candidate_filter_defaults(self) -> None:
        candidate = CandidateFilterConfig()
        self.assertEqual(candidate.min_history_observations, 60)
        self.assertEqual(candidate.min_average_turnover, 0.0)
        self.assertEqual(candidate.max_suspension_ratio, 0.3)

    def test_history_window_defaults(self) -> None:
        window = WindowConfig()
        self.assertEqual(window.lookback_days, 252)
        self.assertEqual(window.min_observations, 60)

    def test_factor_scoring_defaults(self) -> None:
        scoring = FactorScoreConfig(weights=(("dividend_yield", 1.0),))
        self.assertEqual(scoring.missing_policy, MissingPolicy.RENORMALIZE)
        self.assertEqual(scoring.min_available_weight, 0.0)


class DefaultParameterDocumentationTests(unittest.TestCase):
    """Verify the documentation file exists and states its key guarantees."""

    def setUp(self) -> None:
        self.doc = _DOC_PATH.read_text(encoding="utf-8")

    def test_doc_exists(self) -> None:
        self.assertTrue(_DOC_PATH.is_file())

    def test_doc_covers_required_topics(self) -> None:
        for marker in ("成本", "滑点", "流动性", "汇率", "因子缺失"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.doc)

    def test_doc_states_defaults_are_not_market_facts(self) -> None:
        self.assertIn("不是市场事实", self.doc)

    def test_doc_refuses_fx_1_to_1(self) -> None:
        self.assertIn("1:1", self.doc)

    def test_doc_mentions_missing_factor_handling(self) -> None:
        self.assertIn("missing_reason", self.doc)


if __name__ == "__main__":
    unittest.main()
