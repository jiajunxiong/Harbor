"""Three-tier drawdown tests (MVP 4 / SP 4.34-4.36 / 4.46).

Verifies the 5% 预警 / 8% 防御 / 10% 熔断 tier evaluation and its actions.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Currency, NetValue
from harbor.core.paper_drawdown import (
    DrawdownAssessment,
    DrawdownTier,
    PaperDrawdownError,
    evaluate_drawdown,
)


def _nv(day: int, total: float) -> NetValue:
    return NetValue(
        as_of_date=date(2026, 1, day),
        currency=Currency.HKD,
        cash=total,
        securities_value=0.0,
    )


def _series(*totals: float) -> list[NetValue]:
    return [_nv(index + 1, total) for index, total in enumerate(totals)]


class EvaluateDrawdownTests(unittest.TestCase):
    """The tier evaluation (SP 4.34-4.36 / 4.46)."""

    def test_flat_series_no_tier(self) -> None:
        assessment = evaluate_drawdown(net_values=_series(1_000_000.0, 1_000_000.0))
        self.assertIsInstance(assessment, DrawdownAssessment)
        self.assertEqual(assessment.tier, DrawdownTier.NONE)
        self.assertFalse(assessment.triggered)
        self.assertEqual(assessment.drawdown, 0.0)

    def test_warn_tier_at_5_percent(self) -> None:
        assessment = evaluate_drawdown(net_values=_series(1_000_000.0, 950_000.0))
        self.assertEqual(assessment.tier, DrawdownTier.WARN)
        self.assertEqual(assessment.actions, ("stop adding risk positions",))
        self.assertIn("5%", assessment.alert or "")

    def test_warn_tier_above_5_percent(self) -> None:
        assessment = evaluate_drawdown(net_values=_series(1_000_000.0, 940_000.0))
        self.assertEqual(assessment.tier, DrawdownTier.WARN)
        self.assertAlmostEqual(assessment.drawdown, 0.06, places=6)

    def test_defend_tier_at_8_percent(self) -> None:
        assessment = evaluate_drawdown(net_values=_series(1_000_000.0, 920_000.0))
        self.assertEqual(assessment.tier, DrawdownTier.DEFEND)
        self.assertIn("reduce total risk positions to half", assessment.actions)
        self.assertIn("pause new strategies and parameter changes", assessment.actions)

    def test_circuit_tier_at_10_percent(self) -> None:
        assessment = evaluate_drawdown(net_values=_series(1_000_000.0, 900_000.0))
        self.assertEqual(assessment.tier, DrawdownTier.CIRCUIT)
        self.assertIn("freeze new orders", assessment.actions)
        self.assertIn("enter CIRCUIT_BROKEN", assessment.actions)
        self.assertIn("liquidate non-essential risk positions", assessment.actions)
        self.assertIn("freezing new orders", assessment.alert or "")

    def test_circuit_tier_deep_drawdown(self) -> None:
        assessment = evaluate_drawdown(net_values=_series(1_000_000.0, 880_000.0))
        self.assertEqual(assessment.tier, DrawdownTier.CIRCUIT)

    def test_recovered_series_no_tier(self) -> None:
        assessment = evaluate_drawdown(net_values=_series(1_000_000.0, 900_000.0, 980_000.0))
        self.assertEqual(assessment.tier, DrawdownTier.NONE)
        self.assertEqual(assessment.peak_value, 1_000_000.0)

    def test_thresholds_recorded(self) -> None:
        assessment = evaluate_drawdown(net_values=_series(1_000_000.0, 900_000.0))
        self.assertEqual(assessment.thresholds, (0.05, 0.08, 0.10))

    def test_readable(self) -> None:
        assessment = evaluate_drawdown(net_values=_series(1_000_000.0, 900_000.0))
        rendered = assessment.readable()
        self.assertIn("10.00%", rendered)
        self.assertIn("CIRCUIT", rendered)

    def test_invalid_series(self) -> None:
        with self.assertRaises(PaperDrawdownError):
            evaluate_drawdown(net_values=[])
        with self.assertRaises(PaperDrawdownError):
            evaluate_drawdown(net_values=_series(1_000_000.0, 1_000_000.0, 1_000_000.0)[::-1])
        with self.assertRaises(PaperDrawdownError):
            evaluate_drawdown(net_values=_series(0.0, 100.0))
