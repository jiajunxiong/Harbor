"""Earnings quality factor tests (MVP 2 / SP 2.21).

Verifies ROE/net-income/revenue/equity component scoring, the composite score,
missing-value handling, and that reports are used strictly by disclosure
availability (undated or future-dated reports are refused).
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.backtest_interfaces import FundamentalRecord
from harbor.core.factor_earnings_quality import (
    EarningsQualityConfig,
    earnings_quality_factor,
    positive_value_score,
    roe_score,
)

_SYMBOL = "0005.HK"
_DECISION = date(2026, 3, 31)
_REPORT_DATE = date(2025, 12, 31)


def _fundamental(
    roe: float | None = 0.15,
    net_income: float | None = 1.0e9,
    revenue: float | None = 5.0e9,
    total_equity: float | None = 1.0e10,
    available_on: date | None = date(2026, 3, 10),
) -> FundamentalRecord:
    return FundamentalRecord(
        Market.HK,
        _SYMBOL,
        _REPORT_DATE,
        "2025",
        available_on,
        roe=roe,
        net_income=net_income,
        total_equity=total_equity,
        revenue=revenue,
    )


class EarningsQualityConfigTests(unittest.TestCase):
    """Verify factor configuration validation (SP 2.21)."""

    def test_rejects_non_positive_roe_upper(self) -> None:
        with self.assertRaisesRegex(ValueError, "roe_upper"):
            EarningsQualityConfig(roe_upper=0.0)
        with self.assertRaisesRegex(ValueError, "roe_upper"):
            EarningsQualityConfig(roe_upper=-0.1)


class RoiScoreTests(unittest.TestCase):
    """Verify the ROE component (SP 2.21)."""

    def test_none_when_missing(self) -> None:
        self.assertIsNone(roe_score(None))

    def test_non_positive_scores_zero(self) -> None:
        self.assertEqual(roe_score(0.0), 0.0)
        self.assertEqual(roe_score(-0.1), 0.0)

    def test_scales_linearly_to_upper(self) -> None:
        self.assertAlmostEqual(roe_score(0.15), 0.5)
        self.assertEqual(roe_score(0.3), 1.0)

    def test_capped_at_one(self) -> None:
        self.assertEqual(roe_score(0.5), 1.0)

    def test_rejects_non_positive_upper(self) -> None:
        with self.assertRaisesRegex(ValueError, "roe_upper"):
            roe_score(0.1, roe_upper=0.0)


class PositiveValueScoreTests(unittest.TestCase):
    """Verify the positivity component (SP 2.21)."""

    def test_none_when_missing(self) -> None:
        self.assertIsNone(positive_value_score(None))

    def test_positive_scores_one(self) -> None:
        self.assertEqual(positive_value_score(1.0e9), 1.0)

    def test_non_positive_scores_zero(self) -> None:
        self.assertEqual(positive_value_score(0.0), 0.0)
        self.assertEqual(positive_value_score(-1.0e9), 0.0)


class EarningsQualityFactorTests(unittest.TestCase):
    """Verify the composite factor (SP 2.21)."""

    def test_full_quality_score(self) -> None:
        result = earnings_quality_factor(_fundamental(), _DECISION)
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.value, 0.875)
        self.assertAlmostEqual(result.roe_score, 0.5)
        self.assertEqual(result.net_income_score, 1.0)
        self.assertEqual(result.revenue_score, 1.0)
        self.assertEqual(result.equity_score, 1.0)
        self.assertEqual(result.missing_fields, ())
        self.assertIsNone(result.missing_reason)
        self.assertEqual(result.report_date, _REPORT_DATE)
        self.assertEqual(result.available_on, date(2026, 3, 10))

    def test_negative_earnings_penalized(self) -> None:
        result = earnings_quality_factor(_fundamental(roe=0.3, net_income=-1.0e9), _DECISION)
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.value, 0.75)
        self.assertEqual(result.net_income_score, 0.0)

    def test_negative_equity_penalized(self) -> None:
        result = earnings_quality_factor(_fundamental(total_equity=-1.0e9), _DECISION)
        self.assertIsNotNone(result.value)
        self.assertEqual(result.equity_score, 0.0)

    def test_missing_field_skipped_and_listed(self) -> None:
        result = earnings_quality_factor(
            _fundamental(roe=None, net_income=1.0e9, revenue=5.0e9, total_equity=1.0e10),
            _DECISION,
        )
        self.assertIsNotNone(result.value)
        self.assertEqual(result.value, 1.0)
        self.assertIsNone(result.roe_score)
        self.assertEqual(result.missing_fields, ("roe",))
        self.assertIsNone(result.missing_reason)

    def test_all_fields_missing_yields_none_with_reason(self) -> None:
        result = earnings_quality_factor(
            _fundamental(roe=None, net_income=None, revenue=None, total_equity=None),
            _DECISION,
        )
        self.assertIsNone(result.value)
        self.assertIn("no ROE, net income, revenue or equity", result.missing_reason or "")
        self.assertEqual(result.missing_fields, ("roe", "net_income", "revenue", "total_equity"))

    def test_no_financial_data_yields_none_with_reason(self) -> None:
        result = earnings_quality_factor(None, _DECISION)
        self.assertIsNone(result.value)
        self.assertIn("no point-in-time financial data", result.missing_reason or "")
        self.assertIsNone(result.report_date)
        self.assertIsNone(result.available_on)

    def test_undated_report_refused(self) -> None:
        result = earnings_quality_factor(_fundamental(available_on=None), _DECISION)
        self.assertIsNone(result.value)
        self.assertIn("no known disclosure date", result.missing_reason or "")

    def test_future_disclosure_refused(self) -> None:
        result = earnings_quality_factor(_fundamental(available_on=date(2026, 4, 1)), _DECISION)
        self.assertIsNone(result.value)
        self.assertIn("not yet available", result.missing_reason or "")

    def test_disclosure_on_decision_date_accepted(self) -> None:
        result = earnings_quality_factor(_fundamental(available_on=_DECISION), _DECISION)
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.value, 0.875)

    def test_custom_roe_upper(self) -> None:
        result = earnings_quality_factor(
            _fundamental(roe=0.2),
            _DECISION,
            config=EarningsQualityConfig(roe_upper=0.4),
        )
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.roe_score, 0.5)


if __name__ == "__main__":
    unittest.main()
