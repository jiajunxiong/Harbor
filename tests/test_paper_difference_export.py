"""Difference report export tests (MVP 4 / SP 4.82).

Covers the JSON and CSV export of the difference comparison table (SP 4.72)
with stable columns.
"""

import csv
import io
import json
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
    DifferenceMetricsReport,
    compute_difference_metrics,
)
from harbor.core.paper_difference_report import (
    difference_report_csv,
    difference_report_json,
    difference_report_rows,
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


def _report() -> DifferenceMetricsReport:
    return compute_difference_metrics(
        paper_run_id="paper-1",
        assumptions=_snapshot(),
        actuals=(_actual(),),
    )


class DifferenceExportTests(unittest.TestCase):
    """The exportable comparison table (SP 4.82)."""

    def test_rows_have_stable_columns(self) -> None:
        rows = difference_report_rows(_report())
        self.assertEqual(len(rows), 5)
        self.assertEqual(
            list(rows[0].keys()),
            [
                "paper_run_id",
                "market",
                "rebalance_date",
                "symbol",
                "kind",
                "assumed",
                "actual",
                "difference",
                "relative_difference",
            ],
        )
        self.assertEqual(rows[0]["paper_run_id"], "paper-1")
        self.assertEqual(rows[0]["market"], "HK")
        self.assertEqual(rows[0]["rebalance_date"], "2026-01-02")
        self.assertEqual(rows[0]["symbol"], "0001.HK")

    def test_json_export_round_trip(self) -> None:
        payload = json.loads(difference_report_json(_report()))
        self.assertEqual(payload["paper_run_id"], "paper-1")
        self.assertEqual(len(payload["metrics"]), 5)
        self.assertEqual(payload["metrics"][0]["kind"], "slippage")
        self.assertEqual(payload["unmatched"], [])

    def test_csv_export(self) -> None:
        text = difference_report_csv(_report())
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["market"], "HK")
        self.assertEqual(rows[0]["symbol"], "0001.HK")
        self.assertEqual(rows[0]["kind"], "slippage")
        # slippage assumed 10 bps, actual 200 bps -> relative 19.0
        self.assertEqual(rows[0]["relative_difference"], "19.0")
