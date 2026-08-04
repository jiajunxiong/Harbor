"""Adjusted price factor calculation tests."""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.adjustments import (
    ActionTerms,
    AdjustmentEvent,
    compute_adjustment_factors,
    daily_factor_for,
)
from harbor.core.market_registry import CorporateActionType

_WEEK = [
    date(2026, 1, 5),
    date(2026, 1, 6),
    date(2026, 1, 7),
    date(2026, 1, 8),
    date(2026, 1, 9),
]
_CLOSES = {
    date(2026, 1, 5): 100.0,
    date(2026, 1, 6): 101.0,
    date(2026, 1, 7): 102.0,
    date(2026, 1, 8): 103.0,
    date(2026, 1, 9): 104.0,
}


class DailyFactorTests(unittest.TestCase):
    """Verify per-event price adjustment factors."""

    def test_split_two_for_one_halves_prices(self) -> None:
        factor = daily_factor_for(CorporateActionType.SPLIT, ActionTerms(ratio=2.0))
        self.assertAlmostEqual(factor, 0.5)

    def test_consolidation_five_to_one_quintuples_prices(self) -> None:
        factor = daily_factor_for(CorporateActionType.CONSOLIDATION, ActionTerms(ratio=0.2))
        self.assertAlmostEqual(factor, 5.0)

    def test_rights_issue_uses_theoretical_ex_rights_price(self) -> None:
        factor = daily_factor_for(
            CorporateActionType.RIGHTS_ISSUE,
            ActionTerms(ratio=0.5, price=90.0),
            reference_price=101.0,
        )
        self.assertAlmostEqual(factor, (101.0 + 90.0 * 0.5) / (101.0 * 1.5))

    def test_cash_dividend_reduces_price_by_amount(self) -> None:
        factor = daily_factor_for(
            CorporateActionType.DIVIDEND, ActionTerms(price=2.0), reference_price=101.0
        )
        self.assertAlmostEqual(factor, 99.0 / 101.0)

    def test_merger_uses_exchange_ratio(self) -> None:
        factor = daily_factor_for(CorporateActionType.MERGER, ActionTerms(ratio=0.5))
        self.assertAlmostEqual(factor, 2.0)

    def test_spin_off_uses_spin_ratio(self) -> None:
        factor = daily_factor_for(CorporateActionType.SPIN_OFF, ActionTerms(ratio=0.1))
        self.assertAlmostEqual(factor, 1.0 / 1.1)

    def test_tender_offer_does_not_adjust_prices(self) -> None:
        self.assertEqual(daily_factor_for(CorporateActionType.TENDER_OFFER, ActionTerms()), 1.0)

    def test_missing_ratio_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "ratio"):
            daily_factor_for(CorporateActionType.SPLIT, ActionTerms())

    def test_dividend_missing_amount_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "price term"):
            daily_factor_for(CorporateActionType.DIVIDEND, ActionTerms(), reference_price=100.0)

    def test_dividend_missing_reference_price_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "reference price"):
            daily_factor_for(CorporateActionType.DIVIDEND, ActionTerms(price=2.0))

    def test_dividend_exceeding_reference_price_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "below the reference price"):
            daily_factor_for(
                CorporateActionType.DIVIDEND, ActionTerms(price=200.0), reference_price=100.0
            )


class HkAdjustedFactorTests(unittest.TestCase):
    """SP 1.77: Hong Kong rights issue, consolidation, and dividend factors."""

    def test_hk_rights_issue_factors(self) -> None:
        rows = compute_adjustment_factors(
            MarketTarget.HK,
            "0700.HK",
            _WEEK,
            _CLOSES,
            [
                AdjustmentEvent(
                    date(2026, 1, 7),
                    CorporateActionType.RIGHTS_ISSUE,
                    ActionTerms(ratio=0.5, price=90.0),
                )
            ],
        )
        self.assertEqual([row["date"] for row in rows], _WEEK)
        self.assertEqual(rows[0]["market"], "HK")
        self.assertEqual(rows[0]["symbol"], "0700.HK")
        factor = (101.0 + 90.0 * 0.5) / (101.0 * 1.5)
        self.assertAlmostEqual(rows[0]["cumulative_factor"], factor)
        self.assertAlmostEqual(rows[1]["cumulative_factor"], factor)
        self.assertAlmostEqual(rows[2]["cumulative_factor"], 1.0)
        self.assertEqual(rows[0]["daily_factor"], 1.0)
        self.assertAlmostEqual(rows[2]["daily_factor"], factor)

    def test_hk_consolidation_factors(self) -> None:
        rows = compute_adjustment_factors(
            MarketTarget.HK,
            "0005.HK",
            _WEEK,
            _CLOSES,
            [
                AdjustmentEvent(
                    date(2026, 1, 7),
                    CorporateActionType.CONSOLIDATION,
                    ActionTerms(ratio=0.2),
                )
            ],
        )
        self.assertAlmostEqual(rows[0]["cumulative_factor"], 5.0)
        self.assertAlmostEqual(rows[1]["cumulative_factor"], 5.0)
        self.assertAlmostEqual(rows[2]["cumulative_factor"], 1.0)
        self.assertAlmostEqual(rows[2]["daily_factor"], 5.0)

    def test_hk_cash_dividend_factors(self) -> None:
        rows = compute_adjustment_factors(
            MarketTarget.HK,
            "0001.HK",
            _WEEK,
            _CLOSES,
            [
                AdjustmentEvent(
                    date(2026, 1, 7), CorporateActionType.DIVIDEND, ActionTerms(price=2.0)
                )
            ],
        )
        factor = 99.0 / 101.0
        self.assertAlmostEqual(rows[0]["cumulative_factor"], factor)
        self.assertAlmostEqual(rows[1]["cumulative_factor"], factor)
        self.assertAlmostEqual(rows[2]["daily_factor"], factor)
        self.assertAlmostEqual(rows[2]["cumulative_factor"], 1.0)


class UsAdjustedFactorTests(unittest.TestCase):
    """SP 1.78: United States split, merger, and spin-off factors."""

    def test_us_split_factors(self) -> None:
        rows = compute_adjustment_factors(
            MarketTarget.US,
            "AAPL",
            _WEEK,
            _CLOSES,
            [AdjustmentEvent(date(2026, 1, 7), CorporateActionType.SPLIT, ActionTerms(ratio=2.0))],
        )
        self.assertAlmostEqual(rows[0]["cumulative_factor"], 0.5)
        self.assertAlmostEqual(rows[1]["cumulative_factor"], 0.5)
        self.assertAlmostEqual(rows[2]["daily_factor"], 0.5)
        self.assertAlmostEqual(rows[2]["cumulative_factor"], 1.0)

    def test_us_merger_factors(self) -> None:
        rows = compute_adjustment_factors(
            MarketTarget.US,
            "TSLA",
            _WEEK,
            _CLOSES,
            [AdjustmentEvent(date(2026, 1, 7), CorporateActionType.MERGER, ActionTerms(ratio=0.5))],
        )
        self.assertAlmostEqual(rows[0]["cumulative_factor"], 2.0)
        self.assertAlmostEqual(rows[2]["daily_factor"], 2.0)
        self.assertAlmostEqual(rows[2]["cumulative_factor"], 1.0)

    def test_us_spin_off_factors(self) -> None:
        rows = compute_adjustment_factors(
            MarketTarget.US,
            "MSFT",
            _WEEK,
            _CLOSES,
            [
                AdjustmentEvent(
                    date(2026, 1, 7), CorporateActionType.SPIN_OFF, ActionTerms(ratio=0.1)
                )
            ],
        )
        self.assertAlmostEqual(rows[0]["cumulative_factor"], 1.0 / 1.1)
        self.assertAlmostEqual(rows[2]["daily_factor"], 1.0 / 1.1)
        self.assertAlmostEqual(rows[2]["cumulative_factor"], 1.0)

    def test_cumulative_factor_accumulates_future_events(self) -> None:
        rows = compute_adjustment_factors(
            MarketTarget.US,
            "NVDA",
            _WEEK,
            _CLOSES,
            [
                AdjustmentEvent(
                    date(2026, 1, 6), CorporateActionType.SPLIT, ActionTerms(ratio=2.0)
                ),
                AdjustmentEvent(
                    date(2026, 1, 8), CorporateActionType.DIVIDEND, ActionTerms(price=1.0)
                ),
            ],
        )
        split_factor = 0.5
        dividend_factor = (102.0 - 1.0) / 102.0
        self.assertAlmostEqual(rows[0]["cumulative_factor"], split_factor * dividend_factor)
        self.assertAlmostEqual(rows[1]["cumulative_factor"], dividend_factor)
        self.assertAlmostEqual(rows[1]["daily_factor"], split_factor)
        self.assertAlmostEqual(rows[2]["cumulative_factor"], dividend_factor)
        self.assertAlmostEqual(rows[3]["cumulative_factor"], 1.0)
        self.assertAlmostEqual(rows[3]["daily_factor"], dividend_factor)
        self.assertEqual(rows[4]["cumulative_factor"], 1.0)


class AdjustmentValidationTests(unittest.TestCase):
    """Verify market scoping and error handling in factor calculation."""

    def test_hk_rejects_split_event(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported for HK"):
            compute_adjustment_factors(
                MarketTarget.HK,
                "0001.HK",
                _WEEK,
                _CLOSES,
                [
                    AdjustmentEvent(
                        date(2026, 1, 7), CorporateActionType.SPLIT, ActionTerms(ratio=2.0)
                    )
                ],
            )

    def test_us_rejects_rights_issue_event(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported for US"):
            compute_adjustment_factors(
                MarketTarget.US,
                "AAPL",
                _WEEK,
                _CLOSES,
                [
                    AdjustmentEvent(
                        date(2026, 1, 7),
                        CorporateActionType.RIGHTS_ISSUE,
                        ActionTerms(ratio=0.5, price=90.0),
                    )
                ],
            )

    def test_empty_window_returns_no_rows(self) -> None:
        self.assertEqual(
            compute_adjustment_factors(MarketTarget.US, "AAPL", [], {}, []),
            [],
        )

    def test_future_event_is_ignored(self) -> None:
        rows = compute_adjustment_factors(
            MarketTarget.US,
            "AAPL",
            _WEEK,
            _CLOSES,
            [AdjustmentEvent(date(2026, 1, 12), CorporateActionType.SPLIT, ActionTerms(ratio=2.0))],
        )
        self.assertEqual(len(rows), len(_WEEK))
        for row in rows:
            self.assertEqual(row["daily_factor"], 1.0)
            self.assertEqual(row["cumulative_factor"], 1.0)
