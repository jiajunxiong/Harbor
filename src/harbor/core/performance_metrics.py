"""Return and risk metrics (MVP 2 / SP 2.53).

Computes the standard research metrics over a daily net-value series in the
base currency (SP 2.45 :class:`~harbor.core.backtest_domain.NetValue`):

- cumulative return (累计收益): ``last / first - 1``;
- annualized return (年化收益): compounded to ``periods_per_year`` periods;
- annualized volatility (年化波动率): sample std of period returns scaled by
  ``sqrt(periods_per_year)``;
- max drawdown (最大回撤): the worst peak-to-trough decline as a non-negative
  fraction (0.25 means the portfolio fell 25% from a peak);
- Sharpe ratio: annualized excess return over the annualized volatility;
- Calmar ratio: annualized return over the max drawdown;
- downside deviation (下行风险): the below-target semi-deviation of period
  returns, annualized.

The metrics are research-only (不构成投资建议). Every metric is deterministic
and replayable. When the underlying value is undefined (too few observations,
a non-positive net value, zero volatility or a zero drawdown), the module
raises :class:`MetricsError` instead of fabricating a number — matching the
project's never-assume/never-fabricate rule.

Pure core logic: depends only on the backtest domain types (SP 2.45); never
touches storage or CLI code.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import NetValue


class MetricsError(ValueError):
    """Raised when a metric cannot be computed (SP 2.53)."""


@dataclass(frozen=True)
class MetricsConfig:
    """Annualization and risk-free parameters (SP 2.53)."""

    periods_per_year: float = 252.0
    risk_free_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.periods_per_year <= 0:
            raise ValueError("periods_per_year must be positive.")
        if self.risk_free_rate < 0:
            raise ValueError("risk_free_rate must be non-negative.")


@dataclass(frozen=True)
class PerformanceMetrics:
    """The computed return and risk metrics for one run (SP 2.53)."""

    start_date: date
    end_date: date
    periods: int
    cumulative_return: float
    annualized_return: float
    annualized_volatility: float
    max_drawdown: float
    sharpe_ratio: float
    calmar_ratio: float
    downside_deviation: float

    def readable(self) -> str:
        """Render the metrics as a human-readable summary."""
        return (
            f"Performance {self.start_date.isoformat()} -> {self.end_date.isoformat()} "
            f"({self.periods} periods):\n"
            f"  cumulative return: {self.cumulative_return:.4%}\n"
            f"  annualized return: {self.annualized_return:.4%}\n"
            f"  annualized volatility: {self.annualized_volatility:.4%}\n"
            f"  max drawdown: {self.max_drawdown:.4%}\n"
            f"  Sharpe ratio: {self.sharpe_ratio:.4f}\n"
            f"  Calmar ratio: {self.calmar_ratio:.4f}\n"
            f"  downside deviation: {self.downside_deviation:.4%}"
        )


def _values(net_values: Sequence[NetValue]) -> tuple[float, ...]:
    """Return the total-value series, requiring at least two positive points."""
    if len(net_values) < 2:
        raise MetricsError("At least two net values are required to compute metrics.")
    values = tuple(value.total_value for value in net_values)
    if any(value <= 0 for value in values):
        raise MetricsError("Net values must all be positive to compute metrics.")
    return values


def cumulative_return(net_values: Sequence[NetValue]) -> float:
    """Return the cumulative return over the series (SP 2.53)."""
    values = _values(net_values)
    return values[-1] / values[0] - 1.0


def annualized_return(
    net_values: Sequence[NetValue],
    *,
    periods_per_year: float,
) -> float:
    """Return the annualized (compounded) return over the series (SP 2.53)."""
    values = _values(net_values)
    periods = len(values) - 1
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive.")
    growth: float = values[-1] / values[0]
    exponent: float = periods_per_year / periods
    return math.pow(growth, exponent) - 1.0


def _period_returns(values: Sequence[float]) -> tuple[float, ...]:
    """Return the period returns between consecutive values."""
    if len(values) < 3:
        raise MetricsError("At least three net values are required to compute volatility.")
    return tuple(later / earlier - 1.0 for earlier, later in zip(values, values[1:]))


def _sample_std(values: Sequence[float]) -> float:
    """Return the sample standard deviation (ddof=1) of the values."""
    if len(values) < 2:
        raise MetricsError("At least two observations are required for a standard deviation.")
    mean: float = sum(values) / len(values)
    squared_deviations: float = sum((value - mean) ** 2 for value in values)
    variance: float = squared_deviations / (len(values) - 1)
    return math.sqrt(variance)


def annualized_volatility(
    net_values: Sequence[NetValue],
    *,
    periods_per_year: float,
) -> float:
    """Return the annualized volatility of the period returns (SP 2.53)."""
    returns = _period_returns(_values(net_values))
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive.")
    return _sample_std(returns) * math.sqrt(periods_per_year)


def max_drawdown(net_values: Sequence[NetValue]) -> float:
    """Return the worst peak-to-trough decline as a fraction (SP 2.53).

    A non-negative value: ``0.0`` means the series never declined from a peak,
    ``0.25`` a 25% peak-to-trough fall.
    """
    values = _values(net_values)
    peak = values[0]
    worst = 0.0
    for value in values:
        if value > peak:
            peak = value
        drawdown = (peak - value) / peak
        if drawdown > worst:
            worst = drawdown
    return worst


def sharpe_ratio(
    net_values: Sequence[NetValue],
    *,
    periods_per_year: float,
    risk_free_rate: float = 0.0,
) -> float:
    """Return the Sharpe ratio: annualized excess return / annualized vol.

    Raises:
        MetricsError: If the annualized volatility is zero (the ratio is
            undefined and must not be fabricated).
    """
    returns = _period_returns(_values(net_values))
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive.")
    annualized_excess = (sum(returns) / len(returns)) * periods_per_year - risk_free_rate
    volatility = _sample_std(returns) * math.sqrt(periods_per_year)
    if volatility == 0.0:
        raise MetricsError("Sharpe ratio is undefined for zero volatility.")
    return annualized_excess / volatility


def calmar_ratio(
    net_values: Sequence[NetValue],
    *,
    periods_per_year: float,
) -> float:
    """Return the Calmar ratio: annualized return / max drawdown.

    Raises:
        MetricsError: If the max drawdown is zero (the ratio is undefined and
            must not be fabricated).
    """
    annualized = annualized_return(net_values, periods_per_year=periods_per_year)
    drawdown = max_drawdown(net_values)
    if drawdown == 0.0:
        raise MetricsError("Calmar ratio is undefined for zero max drawdown.")
    return annualized / drawdown


def downside_deviation(
    net_values: Sequence[NetValue],
    *,
    periods_per_year: float,
    target_return: float = 0.0,
) -> float:
    """Return the annualized below-target downside deviation (SP 2.53).

    The per-period downside deviation is ``sqrt(mean(min(r - target, 0)**2))``,
    annualized by ``sqrt(periods_per_year)``; ``target_return`` is a per-period
    target (default ``0.0``).
    """
    returns = _period_returns(_values(net_values))
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive.")
    squared = sum(min(return_value - target_return, 0.0) ** 2 for return_value in returns)
    return math.sqrt(squared / len(returns)) * math.sqrt(periods_per_year)


def compute_performance_metrics(
    net_values: Sequence[NetValue],
    *,
    config: MetricsConfig | None = None,
) -> PerformanceMetrics:
    """Compute all return and risk metrics for a net-value series (SP 2.53).

    Args:
        net_values: The daily net-value series in the base currency, in order
            (SP 2.45). At least three points, all positive, are required.
        config: Annualization and risk-free parameters.

    Returns:
        A :class:`PerformanceMetrics` with every metric.

    Raises:
        MetricsError: If the series is too short, contains a non-positive net
            value, or a metric is undefined (zero volatility / zero drawdown).
    """
    if config is None:
        config = MetricsConfig()
    values = _values(net_values)
    if len(values) < 3:
        raise MetricsError("At least three net values are required to compute performance metrics.")
    return PerformanceMetrics(
        start_date=net_values[0].as_of_date,
        end_date=net_values[-1].as_of_date,
        periods=len(values) - 1,
        cumulative_return=cumulative_return(net_values),
        annualized_return=annualized_return(net_values, periods_per_year=config.periods_per_year),
        annualized_volatility=annualized_volatility(
            net_values, periods_per_year=config.periods_per_year
        ),
        max_drawdown=max_drawdown(net_values),
        sharpe_ratio=sharpe_ratio(
            net_values,
            periods_per_year=config.periods_per_year,
            risk_free_rate=config.risk_free_rate,
        ),
        calmar_ratio=calmar_ratio(net_values, periods_per_year=config.periods_per_year),
        downside_deviation=downside_deviation(net_values, periods_per_year=config.periods_per_year),
    )
