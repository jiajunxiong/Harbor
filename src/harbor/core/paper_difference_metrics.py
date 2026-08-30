"""Difference metrics between paper execution and OOS assumptions (SP 4.71).

Quantifies the difference between the paper loop's actual execution (SP 4.70)
and the OOS research assumptions (SP 4.69) for spread, slippage, fill price,
execution latency and cost (差异指标计算, SP 4.71). Every metric is attributed
to a market, a rebalance date (the fill trade date) and a symbol (SP 4.72),
so a single market or rebalance cycle can never be hidden inside an average.

A fill whose market has no recorded assumption is surfaced as unmatched —
annotated, never silently ignored (SP 4.74). The report fingerprint is stable
and excludes the paper run id, so the same inputs replay to the same
difference metrics (SP 4.81).

Pure core logic: depends on the assumption snapshot, the actual metrics, the
paper domain and the SP 2.42 ledger; never touches storage or CLI code.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from harbor.core.backtest_domain import Market
from harbor.core.paper_actual_metrics import ActualExecutionMetrics
from harbor.core.paper_assumption_snapshot import (
    ResearchAssumption,
    ResearchAssumptionSnapshot,
)


class DifferenceKind(StrEnum):
    """The quantified difference categories (SP 4.71)."""

    SPREAD = "spread"
    SLIPPAGE = "slippage"
    FILL_PRICE = "fill_price"
    EXECUTION_LATENCY = "execution_latency"
    COST = "cost"


class DifferenceMetricsError(ValueError):
    """Raised when difference metrics are invalid (SP 4.71)."""


@dataclass(frozen=True)
class DifferenceMetric:
    """One attributed difference (SP 4.71)."""

    market: Market
    rebalance_date: date
    symbol: str
    kind: DifferenceKind
    assumed: float
    actual: float
    difference: float
    relative_difference: float | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise DifferenceMetricsError("symbol must be non-empty.")

    def readable(self) -> str:
        """Render the metric as a compact summary."""
        relative = (
            ""
            if self.relative_difference is None
            else f" ({self.relative_difference:+.2%} relative)"
        )
        return (
            f"{self.market.value}/{self.symbol} on {self.rebalance_date.isoformat()} "
            f"[{self.kind.value}]: assumed {self.assumed:.4f}, actual "
            f"{self.actual:.4f}, difference {self.difference:+.4f}{relative}"
        )


@dataclass(frozen=True)
class DifferenceMetricsReport:
    """The attributed differences for one paper run (SP 4.71)."""

    paper_run_id: str
    metrics: tuple[DifferenceMetric, ...]
    unmatched: tuple[tuple[Market, str, date], ...] = ()

    def fingerprint(self) -> str:
        """Return the stable report fingerprint (run id excluded)."""
        return difference_metrics_fingerprint(self)

    def metrics_for(self, kind: DifferenceKind) -> tuple[DifferenceMetric, ...]:
        """Return the metrics of ``kind``."""
        return tuple(metric for metric in self.metrics if metric.kind is kind)

    def readable(self) -> str:
        """Render the report as a compact summary."""
        lines = [f"difference metrics for {self.paper_run_id}: {len(self.metrics)} metric(s)"]
        for metric in self.metrics:
            lines.append(f"  {metric.readable()}")
        for market, symbol, day in self.unmatched:
            lines.append(
                f"  unmatched: no assumption for {market.value}/{symbol} on "
                f"{day.isoformat()} (annotated, not ignored)"
            )
        return "\n".join(lines)


def _relative(difference: float, assumed: float) -> float | None:
    """Return the relative difference or ``None`` when the baseline is zero."""
    if assumed == 0:
        return None
    return difference / assumed


def _fill_metric(
    *,
    actual: ActualExecutionMetrics,
    assumption: ResearchAssumption,
    kind: DifferenceKind,
    assumed: float,
    actual_value: float,
) -> DifferenceMetric:
    difference = actual_value - assumed
    return DifferenceMetric(
        market=actual.market,
        rebalance_date=actual.trade_date,
        symbol=actual.symbol,
        kind=kind,
        assumed=assumed,
        actual=actual_value,
        difference=difference,
        relative_difference=_relative(difference, assumed),
    )


def _assumed_fill_price(assumption: ResearchAssumption, actual: ActualExecutionMetrics) -> float:
    """Return the assumed fill price: the snapshot's or the actual reference."""
    if assumption.reference_price is not None:
        return assumption.reference_price
    return actual.reference_price


def _cost_bps(actual: ActualExecutionMetrics) -> float:
    """Return the realized all-in cost of a fill in basis points of notional."""
    notional = actual.quantity * actual.fill_price
    if notional == 0:
        return 0.0
    return actual.fee / notional * 10_000.0


def compute_difference_metrics(
    *,
    paper_run_id: str,
    assumptions: ResearchAssumptionSnapshot,
    actuals: tuple[ActualExecutionMetrics, ...],
) -> DifferenceMetricsReport:
    """Compute the attributed difference metrics (SP 4.71).

    For every actual fill, five metrics are produced against its market's
    assumption: slippage, cost, fill price, spread and execution latency. A
    fill whose market has no assumption is appended to ``unmatched`` (SP 4.74)
    and never silently dropped.
    """
    metrics: list[DifferenceMetric] = []
    unmatched: list[tuple[Market, str, date]] = []
    for actual in actuals:
        assumption = assumptions.assumption_for(actual.market)
        if assumption is None:
            unmatched.append((actual.market, actual.symbol, actual.trade_date))
            continue
        metrics.append(
            _fill_metric(
                actual=actual,
                assumption=assumption,
                kind=DifferenceKind.SLIPPAGE,
                assumed=assumption.slippage_bps,
                actual_value=actual.slippage_bps,
            )
        )
        assumed_fill_price = _assumed_fill_price(assumption, actual)
        metrics.append(
            _fill_metric(
                actual=actual,
                assumption=assumption,
                kind=DifferenceKind.FILL_PRICE,
                assumed=assumed_fill_price,
                actual_value=actual.fill_price,
            )
        )
        metrics.append(
            _fill_metric(
                actual=actual,
                assumption=assumption,
                kind=DifferenceKind.SPREAD,
                assumed=assumption.spread_bps,
                actual_value=actual.spread_bps,
            )
        )
        metrics.append(
            _fill_metric(
                actual=actual,
                assumption=assumption,
                kind=DifferenceKind.EXECUTION_LATENCY,
                assumed=assumption.execution_latency_seconds,
                actual_value=actual.execution_latency_seconds,
            )
        )
        metrics.append(
            _fill_metric(
                actual=actual,
                assumption=assumption,
                kind=DifferenceKind.COST,
                assumed=assumption.cost_bps,
                actual_value=_cost_bps(actual),
            )
        )
    return DifferenceMetricsReport(
        paper_run_id=paper_run_id,
        metrics=tuple(metrics),
        unmatched=tuple(unmatched),
    )


def _metric_entry(metric: DifferenceMetric) -> dict[str, object]:
    """Canonical serialization of one metric (SP 4.71)."""
    return {
        "market": metric.market.value,
        "rebalance_date": metric.rebalance_date.isoformat(),
        "symbol": metric.symbol,
        "kind": metric.kind.value,
        "assumed": metric.assumed,
        "actual": metric.actual,
        "difference": metric.difference,
        "relative_difference": metric.relative_difference,
    }


def difference_metrics_fingerprint(report: DifferenceMetricsReport) -> str:
    """Return the stable SHA-256 fingerprint of a report (SP 4.71).

    The paper run id is excluded so the same inputs replay to the same
    fingerprint (SP 4.81).
    """
    payload: dict[str, object] = {
        "metrics": [_metric_entry(metric) for metric in report.metrics],
        "unmatched": [
            [market.value, symbol, day.isoformat()] for market, symbol, day in report.unmatched
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
