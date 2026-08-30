"""Difference verification replay tests (MVP 4 / SP 4.81).

Verifies that the same inputs replay to the same difference metrics and
alerts: identical assumptions and actuals always produce identical reports,
fingerprints and alert sets.
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
from harbor.core.paper_difference_alerts import (
    PreRegisteredThreshold,
    check_difference_thresholds,
    difference_alerts_fingerprint,
)
from harbor.core.paper_difference_metrics import (
    DifferenceKind,
    compute_difference_metrics,
)
from harbor.core.paper_domain import PaperFill

_TRADE = date(2026, 1, 2)


def _snapshot() -> ResearchAssumptionSnapshot:
    assumption = ResearchAssumption(
        market=Market.HK,
        slippage_bps=10.0,
        cost_bps=8.0,
        spread_bps=5.0,
        participation_rate=0.02,
        fill_rule=FillRule.CLOSE,
        execution_latency_seconds=5.0,
        reference_price=50.0,
    )
    return ResearchAssumptionSnapshot(
        version="assumption-1.0",
        source_run_id="oos-1",
        dataset_fingerprint="dataset-abc",
        assumptions=(assumption,),
    )


def _actual() -> ActualExecutionMetrics:
    fill = PaperFill(
        fill_id="fill-1",
        paper_order_id="order-1",
        paper_run_id="paper-1",
        market=Market.HK,
        symbol="0001.HK",
        side=OrderSide.BUY,
        quantity=100.0,
        price=51.0,
        currency=Currency.HKD,
        trade_date=_TRADE,
        fee=5.0,
    )
    return ActualExecutionMetrics(
        paper_run_id="paper-1",
        market=Market.HK,
        symbol="0001.HK",
        trade_date=_TRADE,
        fill_id=fill.fill_id,
        fill_price=51.0,
        quantity=100.0,
        fee=5.0,
        reference_price=50.0,
        slippage_bps=200.0,
        spread_bps=6.0,
        execution_latency_seconds=8.0,
        participation_rate=0.01,
    )


class DifferenceReplayTests(unittest.TestCase):
    """The replayability of difference verification (SP 4.81)."""

    def test_same_inputs_same_metrics_and_fingerprint(self) -> None:
        first = compute_difference_metrics(
            paper_run_id="paper-1",
            assumptions=_snapshot(),
            actuals=(_actual(),),
        )
        second = compute_difference_metrics(
            paper_run_id="paper-1",
            assumptions=_snapshot(),
            actuals=(_actual(),),
        )
        self.assertEqual(first, second)
        self.assertEqual(first.fingerprint(), second.fingerprint())

    def test_same_inputs_same_alerts(self) -> None:
        thresholds = (
            PreRegisteredThreshold(DifferenceKind.SLIPPAGE, 50.0),
            PreRegisteredThreshold(DifferenceKind.FILL_PRICE, 0.5),
        )

        def run(run_id: str) -> tuple[str, str]:
            report = compute_difference_metrics(
                paper_run_id=run_id,
                assumptions=_snapshot(),
                actuals=(_actual(),),
            )
            alerts = check_difference_thresholds(
                paper_run_id=run_id,
                report=report,
                thresholds=thresholds,
            )
            return report.fingerprint(), difference_alerts_fingerprint(alerts)

        first = run("paper-1")
        second = run("paper-2")
        # run id excluded from the replay identity
        self.assertEqual(first, second)
