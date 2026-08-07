"""Return and risk metrics tests (MVP 2 / SP 2.53).

Verifies cumulative return, annualized return, annualized volatility, max
drawdown, Sharpe, Calmar and downside deviation over a daily net-value series
(SP 2.45), and that undefined metrics raise :class:`MetricsError` instead of
fabricating a value.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Currency, NetValue
from harbor.core.performance_metrics import (
    MetricsConfig,
    MetricsError,
    PerformanceMetrics,
    annualized_return,
    annualized_volatility,
    calmar_ratio,
    compute_performance_metrics,
    cumulative_return,
    downside_deviation,
    max_drawdown,
    sharpe_ratio,
)

_DAY = date(2024, 1, 2)


def _net(*values: float, start: date = _DAY) -> tuple[NetValue, ...]:
    """Build a net-value series with one point per trading day."""
    return tuple(
        NetValue(
            as_of_date=start.replace(day=start.day + index),
            currency=Currency.HKD,
            cash=0.0,
            securities_value=value,
        )
        for index, value in enumerate(values)
    )


class CumulativeReturnTests(unittest.TestCase):
    """Verify cumulative return (累计收益)."""

    def test_cumulative_return(self) -> None:
        series = _net(100.0, 110.0, 121.0)
        self.assertAlmostEqual(cumulative_return(series), 0.21, places=6)

    def test_cumulative_return_negative(self) -> None:
        series = _net(100.0, 90.0, 80.0)
        self.assertAlmostEqual(cumulative_return(series), -0.2, places=6)

    def test_requires_two_points(self) -> None:
        with self.assertRaisesRegex(MetricsError, "At least two"):
            cumulative_return(_net(100.0))

    def test_requires_positive_values(self) -> None:
        with self.assertRaisesRegex(MetricsError, "positive"):
            cumulative_return(_net(100.0, 0.0, 110.0))


class AnnualizedReturnTests(unittest.TestCase):
    """Verify annualized return (年化收益)."""

    def test_annualized_return_compounds(self) -> None:
        # Two periods, one per year: annualized == cumulative.
        series = _net(100.0, 121.0)
        self.assertAlmostEqual(annualized_return(series, periods_per_year=1.0), 0.21, places=6)

    def test_annualized_return_monthly_to_annual(self) -> None:
        # +1% per month over 12 months -> ~12.68% annualized.
        series = _net(*[100.0 * (1.01**i) for i in range(13)])
        self.assertAlmostEqual(annualized_return(series, periods_per_year=12.0), 0.1268, places=3)

    def test_requires_positive_periods(self) -> None:
        with self.assertRaises(ValueError):
            annualized_return(_net(100.0, 110.0), periods_per_year=0.0)


class AnnualizedVolatilityTests(unittest.TestCase):
    """Verify annualized volatility (年化波动率)."""

    def test_volatility_scales_with_sqrt(self) -> None:
        # Returns +10% / -10%: sample std ~0.14142; annualized by sqrt(252).
        series = _net(100.0, 110.0, 99.0)
        volatility = annualized_volatility(series, periods_per_year=252.0)
        self.assertGreater(volatility, 0.0)
        # returns = [0.10, -0.10]; mean 0; variance = (0.01+0.01)/1 = 0.02
        expected_std = 0.02**0.5
        self.assertAlmostEqual(volatility, expected_std * 252**0.5, places=6)

    def test_requires_three_points(self) -> None:
        with self.assertRaisesRegex(MetricsError, "At least three"):
            annualized_volatility(_net(100.0, 110.0), periods_per_year=252.0)


class MaxDrawdownTests(unittest.TestCase):
    """Verify max drawdown (最大回撤)."""

    def test_max_drawdown(self) -> None:
        # Peak 120 then trough 90 -> 25% drawdown.
        series = _net(100.0, 120.0, 90.0, 130.0)
        self.assertAlmostEqual(max_drawdown(series), 0.25, places=6)

    def test_max_drawdown_monotonic_up_is_zero(self) -> None:
        series = _net(100.0, 110.0, 121.0)
        self.assertEqual(max_drawdown(series), 0.0)

    def test_max_drawdown_uses_peak_not_start(self) -> None:
        # Starts 100, rises to 150, then falls to 120 -> 20% from the 150 peak.
        series = _net(100.0, 150.0, 120.0)
        self.assertAlmostEqual(max_drawdown(series), 0.2, places=6)


class SharpeRatioTests(unittest.TestCase):
    """Verify the Sharpe ratio."""

    def test_sharpe_ratio_positive(self) -> None:
        series = _net(100.0, 105.0, 110.0)  # steady +5% periods
        ratio = sharpe_ratio(series, periods_per_year=1.0, risk_free_rate=0.0)
        # returns [0.05, 0.0476]; mean 0.0488, std ~0.00168 -> ratio ~29
        self.assertGreater(ratio, 0.0)

    def test_sharpe_ratio_uses_risk_free(self) -> None:
        series = _net(100.0, 100.0, 100.0)  # zero returns, zero vol
        # With zero volatility the ratio is undefined.
        with self.assertRaises(MetricsError):
            sharpe_ratio(series, periods_per_year=1.0, risk_free_rate=0.0)

    def test_zero_volatility_is_refused(self) -> None:
        series = _net(100.0, 100.0, 100.0)
        with self.assertRaisesRegex(MetricsError, "zero volatility"):
            sharpe_ratio(series, periods_per_year=252.0)


class CalmarRatioTests(unittest.TestCase):
    """Verify the Calmar ratio."""

    def test_calmar_ratio(self) -> None:
        series = _net(100.0, 110.0, 99.0)  # annualized return ~ -1%; drawdown 0.10
        ratio = calmar_ratio(series, periods_per_year=2.0)
        self.assertIsInstance(ratio, float)

    def test_zero_drawdown_is_refused(self) -> None:
        series = _net(100.0, 110.0, 121.0)
        with self.assertRaisesRegex(MetricsError, "zero max drawdown"):
            calmar_ratio(series, periods_per_year=252.0)


class DownsideDeviationTests(unittest.TestCase):
    """Verify downside deviation (下行风险)."""

    def test_downside_deviation_only_counts_losses(self) -> None:
        series = _net(100.0, 90.0, 110.0)  # returns [-0.10, +0.2222]
        deviation = downside_deviation(series, periods_per_year=1.0, target_return=0.0)
        # min(-0.10,0)^2 = 0.01; min(0.2222,0)^2 = 0 -> mean 0.005 -> sqrt 0.0707
        self.assertAlmostEqual(deviation, 0.01**0.5 / 2**0.5 * 1.0, places=6)

    def test_downside_deviation_zero_when_all_positive(self) -> None:
        series = _net(100.0, 105.0, 110.0)
        self.assertAlmostEqual(downside_deviation(series, periods_per_year=1.0), 0.0, places=6)


class ComputePerformanceMetricsTests(unittest.TestCase):
    """Verify the consolidated metrics computation (SP 2.53)."""

    def test_compute_all_metrics(self) -> None:
        series = _net(100.0, 120.0, 110.0, 130.0)
        metrics = compute_performance_metrics(series, config=MetricsConfig(periods_per_year=3.0))
        self.assertIsInstance(metrics, PerformanceMetrics)
        self.assertAlmostEqual(metrics.cumulative_return, 0.30, places=6)
        self.assertAlmostEqual(metrics.max_drawdown, 0.083333, places=5)
        self.assertEqual(metrics.periods, 3)
        self.assertIn("cumulative return", metrics.readable())

    def test_too_short_series_is_refused(self) -> None:
        with self.assertRaisesRegex(MetricsError, "At least three"):
            compute_performance_metrics(_net(100.0, 110.0))

    def test_requires_positive_values(self) -> None:
        with self.assertRaisesRegex(MetricsError, "positive"):
            compute_performance_metrics(_net(100.0, 110.0, 0.0))

    def test_zero_volatility_refuses_all(self) -> None:
        series = _net(100.0, 100.0, 100.0)
        with self.assertRaises(MetricsError):
            compute_performance_metrics(series)

    def test_metrics_config_validation(self) -> None:
        with self.assertRaises(ValueError):
            MetricsConfig(periods_per_year=0.0)
        with self.assertRaises(ValueError):
            MetricsConfig(risk_free_rate=-0.01)


if __name__ == "__main__":
    unittest.main()
