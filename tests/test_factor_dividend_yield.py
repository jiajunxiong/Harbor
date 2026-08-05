"""Dividend yield factor tests (MVP 2 / SP 2.17).

Verifies annualized dividend yield computation from regular dividends, that
special dividends are excluded from ranking by default and tracked separately,
and that future-dated dividends can never enter the numerator.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import Dividend
from harbor.core.factor_dividend_yield import (
    DEFAULT_LOOKBACK_DAYS,
    annualize_dividend_sum,
    dividend_yield_factor,
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


class AnnualizeDividendSumTests(unittest.TestCase):
    """Verify window-to-annual basis scaling (SP 2.17)."""

    def test_ttm_window_is_unchanged(self) -> None:
        self.assertEqual(annualize_dividend_sum(3.0, DEFAULT_LOOKBACK_DAYS), 3.0)

    def test_six_month_window_doubles(self) -> None:
        self.assertAlmostEqual(annualize_dividend_sum(1.0, 180), 365.0 / 180.0)

    def test_rejects_non_positive_lookback(self) -> None:
        with self.assertRaisesRegex(ValueError, "lookback"):
            annualize_dividend_sum(1.0, 0)
        with self.assertRaisesRegex(ValueError, "lookback"):
            annualize_dividend_sum(1.0, -1)


class DividendYieldFactorTests(unittest.TestCase):
    """Verify the dividend yield factor (SP 2.17)."""

    def test_regular_dividends_ttm_yield(self) -> None:
        result = dividend_yield_factor(
            (
                _dividend(date(2026, 1, 15), amount=1.0),
                _dividend(date(2026, 3, 1), amount=2.0),
            ),
            latest_price=100.0,
            decision_date=_DECISION,
        )
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.value, 0.03)
        self.assertEqual(result.eligible_sum, 3.0)
        self.assertEqual(result.special_sum, 0.0)
        self.assertEqual(result.dividend_count, 2)
        self.assertEqual(result.latest_price, 100.0)

    def test_special_dividend_excluded_from_ranking_by_default(self) -> None:
        result = dividend_yield_factor(
            (
                _dividend(date(2026, 3, 1), amount=1.0),
                _dividend(date(2026, 3, 15), amount=5.0, is_special=True),
            ),
            latest_price=100.0,
            decision_date=_DECISION,
        )
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.value, 0.01)
        self.assertEqual(result.eligible_sum, 1.0)
        self.assertEqual(result.special_sum, 5.0)
        self.assertEqual(result.dividend_count, 1)

    def test_include_special_adds_special_to_numerator(self) -> None:
        result = dividend_yield_factor(
            (
                _dividend(date(2026, 3, 1), amount=1.0),
                _dividend(date(2026, 3, 15), amount=5.0, is_special=True),
            ),
            latest_price=100.0,
            decision_date=_DECISION,
            include_special=True,
        )
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.value, 0.06)
        self.assertEqual(result.eligible_sum, 6.0)
        self.assertEqual(result.special_sum, 5.0)
        self.assertEqual(result.dividend_count, 2)
        self.assertTrue(result.include_special)

    def test_no_eligible_dividends_yields_zero(self) -> None:
        result = dividend_yield_factor((), latest_price=100.0, decision_date=_DECISION)
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.eligible_sum, 0.0)
        self.assertEqual(result.dividend_count, 0)

    def test_missing_price_yields_none(self) -> None:
        result = dividend_yield_factor(
            (_dividend(date(2026, 3, 1), amount=1.0),),
            latest_price=None,
            decision_date=_DECISION,
        )
        self.assertIsNone(result.value)

    def test_non_positive_price_yields_none(self) -> None:
        result = dividend_yield_factor(
            (_dividend(date(2026, 3, 1), amount=1.0),),
            latest_price=0.0,
            decision_date=_DECISION,
        )
        self.assertIsNone(result.value)

    def test_future_dated_dividend_excluded(self) -> None:
        result = dividend_yield_factor(
            (
                _dividend(date(2026, 4, 1), amount=5.0),
                _dividend(date(2026, 3, 1), amount=1.0),
            ),
            latest_price=100.0,
            decision_date=_DECISION,
        )
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.value, 0.01)
        self.assertEqual(result.dividend_count, 1)

    def test_dividend_outside_lookback_excluded(self) -> None:
        result = dividend_yield_factor(
            (_dividend(date(2025, 6, 1), amount=5.0),),
            latest_price=100.0,
            decision_date=_DECISION,
            lookback_days=90,
        )
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.eligible_sum, 0.0)

    def test_annualization_scales_yield(self) -> None:
        result = dividend_yield_factor(
            (_dividend(date(2026, 3, 1), amount=1.0),),
            latest_price=100.0,
            decision_date=_DECISION,
            lookback_days=180,
        )
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.value, (365.0 / 180.0) / 100.0)

    def test_dividend_on_decision_date_included(self) -> None:
        result = dividend_yield_factor(
            (_dividend(_DECISION, amount=2.0),),
            latest_price=100.0,
            decision_date=_DECISION,
        )
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.value, 0.02)

    def test_rejects_non_positive_lookback(self) -> None:
        with self.assertRaisesRegex(ValueError, "lookback"):
            dividend_yield_factor((), latest_price=100.0, decision_date=_DECISION, lookback_days=0)


if __name__ == "__main__":
    unittest.main()
