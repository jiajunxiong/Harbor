"""OOS performance and risk metrics (MVP 3 / SP 3.38).

Computes the performance and risk metrics on the concatenated OOS equity path
(SP 3.37): returns, volatility, drawdown, Sharpe, Calmar (收益、波动、回撤、
Sharpe、Calmar — MVP 2 SP 2.53), turnover and costs (换手、成本 — SP 2.54),
exposure (暴露 — SP 2.55) and the benchmark excess performance (基准超额表现 —
SP 2.52).

The return/risk metrics are computed directly from the path's net values
(``compute_performance_metrics``); the trade, exposure and benchmark data are
not carried by the path, so they are injected as callables over the path:
``trade_stats_for``, ``exposure_for`` and ``benchmark_return_for``. The
recorded :class:`OosPerformanceReport` re-verifies the excess return is
exactly ``portfolio_return - benchmark_return`` so the report is internally
consistent.

Pure core layer: depends only on the SP 3.37 path and the MVP 2 metric value
types, never on storage, services or CLI.
"""

import hashlib
import json
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace

from harbor.core.backtest_domain import NetValue
from harbor.core.benchmark import excess_return
from harbor.core.exposure import ExposureSeries
from harbor.core.oos_concat import OosEquityPath
from harbor.core.performance_metrics import (
    PerformanceMetrics,
    compute_performance_metrics,
)
from harbor.core.trade_metrics import TradeStats


class OosPerformanceError(ValueError):
    """Raised when OOS performance metrics are invalid (SP 3.38)."""


@dataclass(frozen=True)
class OosPerformanceReport:
    """The OOS performance and risk report on the concatenated path (SP 3.38).

    ``path`` is the SP 3.37 concatenated OOS equity path;
    ``performance`` (收益/波动/回撤/Sharpe/Calmar) the MVP 2 return/risk
    metrics; ``trade_stats`` (换手/成本) the trade and turnover metrics;
    ``exposure`` (暴露) the day-by-day exposure series;
    ``benchmark_return`` the benchmark total return over the OOS horizon and
    ``excess_return`` the benchmark excess performance (基准超额表现).
    """

    path: OosEquityPath
    performance: PerformanceMetrics
    trade_stats: TradeStats
    exposure: ExposureSeries
    benchmark_return: float
    excess_return: float
    dataset_fingerprint: str
    code_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.benchmark_return):
            raise OosPerformanceError("benchmark return must be a finite number.")
        expected_excess = excess_return(
            portfolio_return=self.performance.cumulative_return,
            benchmark_return=self.benchmark_return,
        )
        if abs(self.excess_return - expected_excess) > 1e-9:
            raise OosPerformanceError(
                "excess return is inconsistent with the portfolio and benchmark returns."
            )
        if self.dataset_fingerprint != self.path.dataset_fingerprint:
            raise OosPerformanceError("report dataset fingerprint does not match the OOS path.")
        if self.code_version != self.path.code_version:
            raise OosPerformanceError("report code version does not match the OOS path.")
        if not self.dataset_fingerprint:
            raise OosPerformanceError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise OosPerformanceError("code version must be non-empty.")
        if not self.fingerprint:
            raise OosPerformanceError("OOS performance report fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.path)

    def __iter__(self) -> Iterator[NetValue]:
        return iter(self.path)

    def __getitem__(self, index: int) -> NetValue:
        return self.path[index]

    def readable(self) -> str:
        """Render the OOS performance report as one line."""
        turnover = (
            f"{self.trade_stats.turnover:.4f}" if self.trade_stats.turnover is not None else "n/a"
        )
        return (
            f"OOS performance {self.performance.start_date.isoformat()}.."
            f"{self.performance.end_date.isoformat()} cumulative "
            f"{self.performance.cumulative_return:.4%} sharpe "
            f"{self.performance.sharpe_ratio:.4f} maxdd "
            f"{self.performance.max_drawdown:.4%} turnover {turnover} excess "
            f"{self.excess_return:.4%} fp {self.fingerprint}"
        )


def _exposure_summary(exposure: ExposureSeries) -> dict[str, object]:
    """Compact average-exposure summary for the report fingerprint."""
    points = exposure.points
    if not points:
        return {
            "point_count": 0,
            "average_cash_exposure": None,
            "average_market_exposure": {},
        }
    markets = sorted(
        {market for point in points for market in point.market_exposure},
        key=lambda market: market.value,
    )
    average_cash = sum(point.cash_exposure for point in points) / len(points)
    average_market = {
        market.value: sum(point.market_exposure.get(market, 0.0) for point in points) / len(points)
        for market in markets
    }
    return {
        "point_count": len(points),
        "average_cash_exposure": average_cash,
        "average_market_exposure": average_market,
    }


def compute_oos_metrics(
    path: OosEquityPath,
    *,
    trade_stats_for: Callable[[OosEquityPath], TradeStats],
    exposure_for: Callable[[OosEquityPath], ExposureSeries],
    benchmark_return_for: Callable[[OosEquityPath], float],
) -> OosPerformanceReport:
    """Compute the OOS performance and risk metrics on a concatenated path.

    The return/risk metrics are derived from the path's net values (SP 2.53);
    turnover/costs (SP 2.54), exposure (SP 2.55) and the benchmark return
    (SP 2.52) are injected over the path. The excess return is computed and
    recorded consistently with the portfolio and benchmark returns.
    """
    performance = compute_performance_metrics(path.net_values)
    trade_stats = trade_stats_for(path)
    exposure = exposure_for(path)
    benchmark_return = benchmark_return_for(path)
    if not math.isfinite(benchmark_return):
        raise OosPerformanceError("benchmark return must be a finite number.")
    excess = excess_return(
        portfolio_return=performance.cumulative_return,
        benchmark_return=benchmark_return,
    )
    report = OosPerformanceReport(
        path=path,
        performance=performance,
        trade_stats=trade_stats,
        exposure=exposure,
        benchmark_return=benchmark_return,
        excess_return=excess,
        dataset_fingerprint=path.dataset_fingerprint,
        code_version=path.code_version,
        fingerprint="unfingerprinted",
    )
    return replace(report, fingerprint=oos_metrics_fingerprint(report))


def oos_metrics_json(report: OosPerformanceReport) -> str:
    """Return a stable, key-sorted JSON serialization of a performance report.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    performance = report.performance
    trade = report.trade_stats
    payload: dict[str, object] = {
        "dataset_fingerprint": report.dataset_fingerprint,
        "code_version": report.code_version,
        "performance": {
            "start_date": performance.start_date.isoformat(),
            "end_date": performance.end_date.isoformat(),
            "periods": performance.periods,
            "cumulative_return": performance.cumulative_return,
            "annualized_return": performance.annualized_return,
            "annualized_volatility": performance.annualized_volatility,
            "max_drawdown": performance.max_drawdown,
            "sharpe_ratio": performance.sharpe_ratio,
            "calmar_ratio": performance.calmar_ratio,
            "downside_deviation": performance.downside_deviation,
        },
        "trade": {
            "fill_count": trade.fill_count,
            "turnover": trade.turnover,
            "total_fees_base": trade.total_fees_base,
            "slippage_cost_base": trade.slippage_cost_base,
        },
        "exposure": _exposure_summary(report.exposure),
        "benchmark_return": report.benchmark_return,
        "excess_return": report.excess_return,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def oos_metrics_fingerprint(report: OosPerformanceReport) -> str:
    """Return the stable SHA-256 fingerprint of a performance report (SP 3.38)."""
    return hashlib.sha256(oos_metrics_json(report).encode("utf-8")).hexdigest()
