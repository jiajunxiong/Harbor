"""Difference thresholds / alerts / coverage tests (MVP 4 / SP 4.79).

Covers threshold triggering, alert recording, recovery when the difference
returns within the threshold, and coverage-insufficiency annotation (SP 4.74).
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
    DifferenceAlert,
    DifferenceAlertError,
    PreRegisteredThreshold,
    assess_paper_coverage,
    check_difference_thresholds,
    coverage_insufficient_alerts,
    difference_alerts_fingerprint,
)
from harbor.core.paper_difference_metrics import (
    DifferenceKind,
    DifferenceMetricsReport,
    compute_difference_metrics,
)
from harbor.core.paper_domain import PaperFill

_TRADE = date(2026, 1, 2)


def _assumption(**overrides: object) -> ResearchAssumption:
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
    return ResearchAssumption(market=Market.HK, **values)  # type: ignore[arg-type]


def _snapshot() -> ResearchAssumptionSnapshot:
    return ResearchAssumptionSnapshot(
        version="assumption-1.0",
        source_run_id="oos-1",
        dataset_fingerprint="dataset-abc",
        assumptions=(_assumption(),),
    )


def _actual(
    fill_price: float = 51.0,
    reference_price: float = 50.0,
    spread_bps: float = 6.0,
    latency: float = 8.0,
    fee: float = 5.0,
) -> ActualExecutionMetrics:
    fill = PaperFill(
        fill_id="fill-1",
        paper_order_id="order-1",
        paper_run_id="paper-1",
        market=Market.HK,
        symbol="0001.HK",
        side=OrderSide.BUY,
        quantity=100.0,
        price=fill_price,
        currency=Currency.HKD,
        trade_date=_TRADE,
        fee=fee,
    )
    slippage = (fill_price - reference_price) / reference_price * 10_000.0
    return ActualExecutionMetrics(
        paper_run_id="paper-1",
        market=Market.HK,
        symbol="0001.HK",
        trade_date=_TRADE,
        fill_id=fill.fill_id,
        fill_price=fill_price,
        quantity=100.0,
        fee=fee,
        reference_price=reference_price,
        slippage_bps=slippage,
        spread_bps=spread_bps,
        execution_latency_seconds=latency,
        participation_rate=100.0 / 10_000,
    )


def _report(actuals: tuple[ActualExecutionMetrics, ...] = (_actual(),)) -> DifferenceMetricsReport:
    return compute_difference_metrics(
        paper_run_id="paper-1",
        assumptions=_snapshot(),
        actuals=actuals,
    )


class ThresholdTests(unittest.TestCase):
    """The pre-registered thresholds and alerts (SP 4.73 / 4.79)."""

    def test_threshold_exceeded_records_alert(self) -> None:
        report = _report()
        alerts = check_difference_thresholds(
            paper_run_id="paper-1",
            report=report,
            thresholds=(
                PreRegisteredThreshold(DifferenceKind.SLIPPAGE, 50.0),
                PreRegisteredThreshold(DifferenceKind.FILL_PRICE, 0.5),
                PreRegisteredThreshold(DifferenceKind.EXECUTION_LATENCY, 2.0),
            ),
        )
        self.assertEqual(len(alerts), 3)

    def test_alert_message_attributes(self) -> None:
        report = _report()
        alerts = check_difference_thresholds(
            paper_run_id="paper-1",
            report=report,
            thresholds=(PreRegisteredThreshold(DifferenceKind.SLIPPAGE, 50.0),),
        )
        alert = alerts[0]
        self.assertIsInstance(alert, DifferenceAlert)
        self.assertEqual(alert.paper_run_id, "paper-1")
        self.assertEqual(alert.metric.market, Market.HK)
        self.assertEqual(alert.metric.symbol, "0001.HK")
        self.assertEqual(alert.threshold, 50.0)
        self.assertIn("registered threshold", alert.readable())

    def test_within_threshold_no_alert(self) -> None:
        report = _report()
        alerts = check_difference_thresholds(
            paper_run_id="paper-1",
            report=report,
            thresholds=(PreRegisteredThreshold(DifferenceKind.SLIPPAGE, 10_000.0),),
        )
        self.assertEqual(len(alerts), 0)

    def test_recovery_when_difference_returns(self) -> None:
        # large difference -> alert
        triggered = _report(actuals=(_actual(fill_price=55.0),))
        alerts = check_difference_thresholds(
            paper_run_id="paper-1",
            report=triggered,
            thresholds=(PreRegisteredThreshold(DifferenceKind.SLIPPAGE, 50.0),),
        )
        self.assertEqual(len(alerts), 1)
        # smaller difference -> recovered, no alert
        recovered = _report(actuals=(_actual(fill_price=50.1),))
        alerts = check_difference_thresholds(
            paper_run_id="paper-1",
            report=recovered,
            thresholds=(PreRegisteredThreshold(DifferenceKind.SLIPPAGE, 50.0),),
        )
        self.assertEqual(len(alerts), 0)

    def test_unregistered_kind_skipped(self) -> None:
        report = _report()
        alerts = check_difference_thresholds(
            paper_run_id="paper-1",
            report=report,
            thresholds=(PreRegisteredThreshold(DifferenceKind.COST, 0.01),),
        )
        # cost difference ~1.8 bps > 0.01 -> 1 alert, others skipped
        self.assertEqual(len(alerts), 1)

    def test_negative_threshold_rejected(self) -> None:
        with self.assertRaises(DifferenceAlertError):
            PreRegisteredThreshold(DifferenceKind.SLIPPAGE, -1.0)

    def test_alert_fingerprint_excludes_run_id(self) -> None:
        first = check_difference_thresholds(
            paper_run_id="paper-1",
            report=_report(),
            thresholds=(PreRegisteredThreshold(DifferenceKind.SLIPPAGE, 1.0),),
        )
        second = check_difference_thresholds(
            paper_run_id="paper-2",
            report=_report(),
            thresholds=(PreRegisteredThreshold(DifferenceKind.SLIPPAGE, 1.0),),
        )
        self.assertEqual(
            difference_alerts_fingerprint(first), difference_alerts_fingerprint(second)
        )


class CoverageTests(unittest.TestCase):
    """The coverage-insufficiency annotation (SP 4.74)."""

    def test_full_coverage_sufficient(self) -> None:
        assessment = assess_paper_coverage(
            paper_run_id="paper-1",
            market="HK",
            filled_days=20,
            expected_days=20,
        )
        self.assertTrue(assessment.sufficient)
        self.assertAlmostEqual(assessment.coverage_pct, 1.0)
        self.assertEqual(assessment.notes, ())

    def test_partial_coverage_insufficient(self) -> None:
        assessment = assess_paper_coverage(
            paper_run_id="paper-1",
            market="HK",
            filled_days=10,
            expected_days=20,
            min_coverage_pct=0.9,
        )
        self.assertFalse(assessment.sufficient)
        self.assertAlmostEqual(assessment.coverage_pct, 0.5)
        self.assertEqual(len(assessment.notes), 1)

    def test_missing_data_always_insufficient(self) -> None:
        assessment = assess_paper_coverage(
            paper_run_id="paper-1",
            market="HK",
            filled_days=20,
            expected_days=20,
            missing_data=("quotes:2026-02-01",),
        )
        self.assertFalse(assessment.sufficient)
        self.assertIn("missing data", assessment.notes[0])

    def test_invalid_inputs_rejected(self) -> None:
        with self.assertRaises(DifferenceAlertError):
            assess_paper_coverage(
                paper_run_id="",
                market="HK",
                filled_days=1,
                expected_days=1,
            )
        with self.assertRaises(DifferenceAlertError):
            assess_paper_coverage(
                paper_run_id="p",
                market="HK",
                filled_days=2,
                expected_days=1,
            )
        with self.assertRaises(DifferenceAlertError):
            assess_paper_coverage(
                paper_run_id="p",
                market="HK",
                filled_days=1,
                expected_days=0,
            )

    def test_insufficient_alerts(self) -> None:
        ok = assess_paper_coverage(
            paper_run_id="p",
            market="HK",
            filled_days=20,
            expected_days=20,
        )
        bad = assess_paper_coverage(
            paper_run_id="p",
            market="US",
            filled_days=1,
            expected_days=20,
            min_coverage_pct=0.9,
        )
        alerts = coverage_insufficient_alerts((ok, bad))
        self.assertEqual(len(alerts), 1)
        self.assertIn("US", alerts[0])

    def test_readable(self) -> None:
        assessment = assess_paper_coverage(
            paper_run_id="p",
            market="HK",
            filled_days=10,
            expected_days=20,
            min_coverage_pct=0.9,
        )
        self.assertIn("insufficient", assessment.readable())
