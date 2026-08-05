"""Dividend sustainability factor tests (MVP 2 / SP 2.18).

Verifies the continuity and payout-ratio components, that a missing payout
(no financial data or no positive net income) yields ``None`` with a readable
reason, that special dividends are excluded from continuity by default, and
that future-dated dividends can never enter the computation.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import Dividend, FundamentalRecord
from harbor.core.factor_dividend_sustainability import (
    DividendSustainabilityConfig,
    dividend_sustainability_factor,
    payout_ratio_score,
)

_SYMBOL = "0005.HK"
_DECISION = date(2026, 3, 31)


def _dividend(
    ex_date: date,
    amount: float = 1.0,
    is_special: bool = False,
) -> Dividend:
    return Dividend(
        Market.HK,
        _SYMBOL,
        amount,
        Currency.HKD,
        ex_date,
        is_special=is_special,
    )


def _fundamental(net_income: float) -> FundamentalRecord:
    return FundamentalRecord(
        Market.HK,
        _SYMBOL,
        date(2025, 12, 31),
        "2025",
        available_on=date(2026, 3, 10),
        roe=0.15,
        net_income=net_income,
        total_equity=1.0e10,
        revenue=5.0e9,
    )


class DividendSustainabilityConfigTests(unittest.TestCase):
    """Verify factor configuration validation (SP 2.18)."""

    def test_rejects_non_positive_lookback(self) -> None:
        with self.assertRaisesRegex(ValueError, "lookback"):
            DividendSustainabilityConfig(lookback_days=0)

    def test_rejects_non_positive_expected_payments(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected_payments"):
            DividendSustainabilityConfig(expected_payments=0)

    def test_rejects_max_sustainable_at_or_below_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_sustainable"):
            DividendSustainabilityConfig(max_sustainable_payout=1.0)


class PayoutRatioScoreTests(unittest.TestCase):
    """Verify payout coverage scoring (SP 2.18)."""

    def test_zero_or_negative_payout_scores_zero(self) -> None:
        self.assertEqual(payout_ratio_score(0.0), 0.0)
        self.assertEqual(payout_ratio_score(-0.1), 0.0)

    def test_fully_covered_payout_scores_one(self) -> None:
        self.assertEqual(payout_ratio_score(0.5), 1.0)
        self.assertEqual(payout_ratio_score(1.0), 1.0)

    def test_over_covered_payout_declines(self) -> None:
        self.assertAlmostEqual(payout_ratio_score(1.5), 0.5)
        self.assertEqual(payout_ratio_score(2.0), 0.0)
        self.assertEqual(payout_ratio_score(3.0), 0.0)

    def test_custom_max_sustainable(self) -> None:
        self.assertAlmostEqual(payout_ratio_score(1.5, max_sustainable=3.0), 0.75)

    def test_rejects_max_sustainable_at_or_below_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_sustainable"):
            payout_ratio_score(1.0, max_sustainable=1.0)


class DividendSustainabilityFactorTests(unittest.TestCase):
    """Verify the composite factor (SP 2.18)."""

    def test_consistent_payer_fully_covered(self) -> None:
        dividends = tuple(_dividend(date(2026, 1, 15), amount=1.0) for _ in range(4))
        result = dividend_sustainability_factor(
            dividends, _fundamental(net_income=100.0), _DECISION
        )
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.value, 1.0)
        self.assertEqual(result.continuity_score, 1.0)
        self.assertEqual(result.regular_payments, 4)
        self.assertAlmostEqual(result.regular_sum, 4.0)
        self.assertIsNotNone(result.payout_ratio)
        self.assertAlmostEqual(result.payout_ratio, 0.04)
        self.assertEqual(result.payout_ratio_score, 1.0)
        self.assertIsNone(result.missing_reason)

    def test_partial_continuity_lowers_score(self) -> None:
        dividends = (
            _dividend(date(2026, 1, 15), amount=1.0),
            _dividend(date(2026, 3, 1), amount=1.0),
        )
        result = dividend_sustainability_factor(
            dividends, _fundamental(net_income=100.0), _DECISION
        )
        self.assertIsNotNone(result.value)
        self.assertEqual(result.continuity_score, 0.5)
        self.assertAlmostEqual(result.value, 0.75)

    def test_continuity_capped_at_one(self) -> None:
        dividends = tuple(_dividend(date(2026, 1, 15), amount=1.0) for _ in range(6))
        result = dividend_sustainability_factor(
            dividends, _fundamental(net_income=100.0), _DECISION
        )
        self.assertEqual(result.continuity_score, 1.0)

    def test_unsustainable_high_payout_scores_lower(self) -> None:
        dividends = (
            _dividend(date(2026, 1, 15), amount=7.5),
            _dividend(date(2026, 3, 1), amount=7.5),
        )
        result = dividend_sustainability_factor(dividends, _fundamental(net_income=10.0), _DECISION)
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.payout_ratio, 1.5)
        self.assertAlmostEqual(result.payout_ratio_score, 0.5)
        self.assertEqual(result.continuity_score, 0.5)
        self.assertAlmostEqual(result.value, 0.5)

    def test_no_financial_data_yields_none_with_reason(self) -> None:
        dividends = (_dividend(date(2026, 1, 15), amount=1.0),)
        result = dividend_sustainability_factor(dividends, None, _DECISION)
        self.assertIsNone(result.value)
        self.assertEqual(result.continuity_score, 0.25)
        self.assertIsNone(result.payout_ratio)
        self.assertIsNone(result.payout_ratio_score)
        self.assertIn("no point-in-time financial data", result.missing_reason or "")

    def test_no_positive_net_income_yields_none_with_reason(self) -> None:
        dividends = (_dividend(date(2026, 1, 15), amount=1.0),)
        result = dividend_sustainability_factor(dividends, _fundamental(net_income=0.0), _DECISION)
        self.assertIsNone(result.value)
        self.assertIn("no positive net income", result.missing_reason or "")

    def test_non_payer_with_earnings_scores_zero(self) -> None:
        result = dividend_sustainability_factor((), _fundamental(net_income=100.0), _DECISION)
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.regular_payments, 0)
        self.assertIn("no regular dividends paid", result.missing_reason or "")

    def test_special_dividend_excluded_from_continuity_by_default(self) -> None:
        dividends = (
            _dividend(date(2026, 1, 15), amount=1.0),
            _dividend(date(2026, 3, 1), amount=5.0, is_special=True),
        )
        result = dividend_sustainability_factor(
            dividends, _fundamental(net_income=100.0), _DECISION
        )
        self.assertEqual(result.regular_payments, 1)
        self.assertEqual(result.continuity_score, 0.25)
        self.assertEqual(result.special_sum, 5.0)
        self.assertAlmostEqual(result.regular_sum, 1.0)

    def test_include_special_counts_special_payments(self) -> None:
        dividends = (
            _dividend(date(2026, 1, 15), amount=1.0),
            _dividend(date(2026, 3, 1), amount=5.0, is_special=True),
        )
        result = dividend_sustainability_factor(
            dividends,
            _fundamental(net_income=100.0),
            _DECISION,
            config=DividendSustainabilityConfig(include_special=True),
        )
        self.assertEqual(result.regular_payments, 2)
        self.assertEqual(result.continuity_score, 0.5)
        self.assertAlmostEqual(result.regular_sum, 6.0)
        self.assertTrue(result.include_special)

    def test_future_dated_dividend_excluded(self) -> None:
        dividends = (
            _dividend(date(2026, 1, 15), amount=1.0),
            _dividend(date(2026, 4, 1), amount=5.0),
        )
        result = dividend_sustainability_factor(
            dividends, _fundamental(net_income=100.0), _DECISION
        )
        self.assertEqual(result.regular_payments, 1)
        self.assertAlmostEqual(result.regular_sum, 1.0)

    def test_out_of_window_dividend_excluded(self) -> None:
        dividends = (_dividend(date(2025, 6, 1), amount=5.0),)
        result = dividend_sustainability_factor(
            dividends,
            _fundamental(net_income=100.0),
            _DECISION,
            config=DividendSustainabilityConfig(lookback_days=90),
        )
        self.assertEqual(result.regular_payments, 0)
        self.assertEqual(result.regular_sum, 0.0)

    def test_annualized_payout_uses_lookback(self) -> None:
        dividends = (_dividend(date(2026, 3, 1), amount=1.0),)
        result = dividend_sustainability_factor(
            dividends,
            _fundamental(net_income=10.0),
            _DECISION,
            config=DividendSustainabilityConfig(lookback_days=180),
        )
        self.assertIsNotNone(result.payout_ratio)
        self.assertAlmostEqual(result.payout_ratio, (365.0 / 180.0) / 10.0)


if __name__ == "__main__":
    unittest.main()
