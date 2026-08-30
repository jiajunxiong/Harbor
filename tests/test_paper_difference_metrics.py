"""Difference metrics tests (MVP 4 / SP 4.78).

Covers the difference computation (SP 4.71), the attribution by market /
rebalance date / symbol (SP 4.72), the benchmark comparison and the missing
data handling (SP 4.74).
"""

import unittest
from datetime import date

from harbor.core.backtest_config import FillRule
from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.paper_actual_metrics import ActualExecutionMetrics
from harbor.core.paper_assumption_snapshot import (
    ResearchAssumption,
    ResearchAssumptionSnapshot,
)
from harbor.core.paper_difference_metrics import (
    DifferenceKind,
    DifferenceMetric,
    compute_difference_metrics,
    difference_metrics_fingerprint,
)
from harbor.core.paper_domain import PaperFill

_TRADE = date(2026, 1, 2)


def _assumption(market: Market, **overrides: object) -> ResearchAssumption:
    values: dict[str, object] = {
        "slippage_bps": 10.0,
        "cost_bps": 8.0,
        "spread_bps": 5.0,
        "participation_rate": 0.02,
        "fill_rule": FillRule.CLOSE,
        "execution_latency_seconds": 5.0,
        "reference_price": 50.0,
    }
    values.update(overrides)
    return ResearchAssumption(market=market, **values)  # type: ignore[arg-type]


def _snapshot(*assumptions: ResearchAssumption) -> ResearchAssumptionSnapshot:
    return ResearchAssumptionSnapshot(
        version="assumption-1.0",
        source_run_id="oos-1",
        dataset_fingerprint="dataset-abc",
        assumptions=assumptions,
    )


def _actual(
    market: Market = Market.HK,
    symbol: str = "0001.HK",
    fill_price: float = 51.0,
    reference_price: float = 50.0,
    spread_bps: float = 6.0,
    latency: float = 8.0,
    fee: float = 5.0,
    quantity: float = 100.0,
) -> ActualExecutionMetrics:
    fill = PaperFill(
        fill_id="fill-1",
        paper_order_id="order-1",
        paper_run_id="paper-1",
        market=market,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=quantity,
        price=fill_price,
        currency=Currency.HKD,
        trade_date=_TRADE,
        fee=fee,
    )
    slippage = (fill_price - reference_price) / reference_price * 10_000.0
    return ActualExecutionMetrics(
        paper_run_id="paper-1",
        market=market,
        symbol=symbol,
        trade_date=_TRADE,
        fill_id=fill.fill_id,
        fill_price=fill_price,
        quantity=quantity,
        fee=fee,
        reference_price=reference_price,
        slippage_bps=slippage,
        spread_bps=spread_bps,
        execution_latency_seconds=latency,
        participation_rate=quantity / 10_000,
    )


class DifferenceMetricsTests(unittest.TestCase):
    """The quantified differences (SP 4.71 / 4.78)."""

    def test_five_metrics_per_fill(self) -> None:
        report = compute_difference_metrics(
            paper_run_id="paper-1",
            assumptions=_snapshot(_assumption(Market.HK)),
            actuals=(_actual(),),
        )
        self.assertEqual(len(report.metrics), 5)
        kinds = {metric.kind for metric in report.metrics}
        self.assertEqual(
            kinds,
            {
                DifferenceKind.SLIPPAGE,
                DifferenceKind.COST,
                DifferenceKind.FILL_PRICE,
                DifferenceKind.SPREAD,
                DifferenceKind.EXECUTION_LATENCY,
            },
        )

    def test_slippage_and_fill_price_differences(self) -> None:
        report = compute_difference_metrics(
            paper_run_id="paper-1",
            assumptions=_snapshot(_assumption(Market.HK)),
            actuals=(_actual(fill_price=51.0, reference_price=50.0),),
        )
        slippage = next(
            metric for metric in report.metrics if metric.kind is DifferenceKind.SLIPPAGE
        )
        self.assertIsInstance(slippage, DifferenceMetric)
        self.assertAlmostEqual(slippage.assumed, 10.0)
        self.assertAlmostEqual(slippage.actual, 200.0)
        self.assertAlmostEqual(slippage.difference, 190.0)
        fill_price = next(
            metric for metric in report.metrics if metric.kind is DifferenceKind.FILL_PRICE
        )
        self.assertAlmostEqual(fill_price.assumed, 50.0)
        self.assertAlmostEqual(fill_price.actual, 51.0)

    def test_cost_difference_from_fee(self) -> None:
        # fee 5.0 on notional 100 * 51 = 5100 -> ~9.80 bps
        report = compute_difference_metrics(
            paper_run_id="paper-1",
            assumptions=_snapshot(_assumption(Market.HK, cost_bps=8.0)),
            actuals=(_actual(fee=5.0, quantity=100.0, fill_price=51.0),),
        )
        cost = next(metric for metric in report.metrics if metric.kind is DifferenceKind.COST)
        self.assertAlmostEqual(cost.assumed, 8.0)
        self.assertAlmostEqual(cost.actual, 5.0 / 5100 * 10_000.0)
        self.assertAlmostEqual(cost.difference, cost.actual - cost.assumed)

    def test_latency_difference(self) -> None:
        report = compute_difference_metrics(
            paper_run_id="paper-1",
            assumptions=_snapshot(_assumption(Market.HK, execution_latency_seconds=5.0)),
            actuals=(_actual(latency=8.0),),
        )
        latency = next(
            metric for metric in report.metrics if metric.kind is DifferenceKind.EXECUTION_LATENCY
        )
        self.assertAlmostEqual(latency.difference, 3.0)

    def test_missing_assumption_annotated_not_dropped(self) -> None:
        report = compute_difference_metrics(
            paper_run_id="paper-1",
            assumptions=_snapshot(_assumption(Market.HK)),
            actuals=(_actual(market=Market.US, symbol="AAPL"),),
        )
        self.assertEqual(len(report.metrics), 0)
        self.assertEqual(len(report.unmatched), 1)
        market, symbol, day = report.unmatched[0]
        self.assertEqual(market, Market.US)
        self.assertEqual(symbol, "AAPL")
        self.assertEqual(day, _TRADE)
        self.assertIn("unmatched", report.readable())

    def test_fingerprint_excludes_run_id(self) -> None:
        first = compute_difference_metrics(
            paper_run_id="paper-1",
            assumptions=_snapshot(_assumption(Market.HK)),
            actuals=(_actual(),),
        )
        second = compute_difference_metrics(
            paper_run_id="paper-2",
            assumptions=_snapshot(_assumption(Market.HK)),
            actuals=(_actual(),),
        )
        self.assertEqual(first.fingerprint(), second.fingerprint())
        self.assertEqual(difference_metrics_fingerprint(first), first.fingerprint())

    def test_report_readable(self) -> None:
        report = compute_difference_metrics(
            paper_run_id="paper-1",
            assumptions=_snapshot(_assumption(Market.HK)),
            actuals=(_actual(),),
        )
        self.assertIn("difference metrics for paper-1", report.readable())


class MissingDataTests(unittest.TestCase):
    """The missing-data handling (SP 4.78 / 4.74)."""

    def test_metrics_for_kind(self) -> None:
        report = compute_difference_metrics(
            paper_run_id="paper-1",
            assumptions=_snapshot(_assumption(Market.HK)),
            actuals=(_actual(),),
        )
        slippages = report.metrics_for(DifferenceKind.SLIPPAGE)
        self.assertEqual(len(slippages), 1)
        self.assertEqual(slippages[0].kind, DifferenceKind.SLIPPAGE)
