"""Post-correction recalculation mechanism tests."""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.adjustments import ActionTerms, AdjustmentEvent
from harbor.core.equity import EntitlementEvent
from harbor.core.market_registry import CorporateActionType
from harbor.core.recalculation import recalculate

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


class HkRecalculationTests(unittest.TestCase):
    """SP 1.83: Hong Kong post-correction recalculation."""

    def test_hk_recalculates_factors_and_equity_after_correction(self) -> None:
        result = recalculate(
            MarketTarget.HK,
            "0700.HK",
            _WEEK,
            _CLOSES,
            adjustment_events=[
                AdjustmentEvent(
                    date(2026, 1, 7),
                    CorporateActionType.RIGHTS_ISSUE,
                    ActionTerms(ratio=0.5, price=90.0),
                )
            ],
            entitlement_events=[
                EntitlementEvent(
                    "0700.HK-div-1",
                    CorporateActionType.DIVIDEND,
                    terms=ActionTerms(price=2.0),
                    record_date=date(2026, 1, 7),
                ),
                EntitlementEvent(
                    "0700.HK-rights-1",
                    CorporateActionType.RIGHTS_ISSUE,
                    terms=ActionTerms(ratio=0.5, price=90.0),
                    record_date=date(2026, 1, 7),
                ),
            ],
            position_date=date(2026, 1, 5),
            quantity=100.0,
        )

        self.assertEqual(result["market"], "HK")
        self.assertEqual(result["symbol"], "0700.HK")
        self.assertTrue(result["recalculated"])
        factors = result["adjusted_factors"]
        self.assertEqual(len(factors), len(_WEEK))
        factor = (101.0 + 90.0 * 0.5) / (101.0 * 1.5)
        self.assertAlmostEqual(factors[0]["cumulative_factor"], factor)
        self.assertAlmostEqual(factors[2]["daily_factor"], factor)

        equity = {row["action_id"]: row for row in result["equity_events"]}
        self.assertEqual(len(equity), 2)
        self.assertEqual(equity["0700.HK-div-1"]["cash_amount"], 200.0)
        self.assertEqual(equity["0700.HK-rights-1"]["entitled_quantity"], 50.0)

    def test_hk_rejects_unsupported_event_after_correction(self) -> None:
        result = recalculate(
            MarketTarget.HK,
            "0001.HK",
            _WEEK,
            _CLOSES,
            adjustment_events=[
                AdjustmentEvent(date(2026, 1, 7), CorporateActionType.SPLIT, ActionTerms(ratio=2.0))
            ],
            entitlement_events=[],
        )
        self.assertFalse(result["recalculated"])
        self.assertEqual(result["review_items"][0]["reason"], "action_type_not_supported")

    def test_hk_flags_event_with_missing_terms(self) -> None:
        result = recalculate(
            MarketTarget.HK,
            "0700.HK",
            _WEEK,
            _CLOSES,
            adjustment_events=[
                AdjustmentEvent(
                    date(2026, 1, 7),
                    CorporateActionType.RIGHTS_ISSUE,
                    ActionTerms(price=90.0),
                )
            ],
            entitlement_events=[],
        )
        self.assertFalse(result["recalculated"])
        self.assertEqual(result["review_items"][0]["reason"], "missing_terms")


class UsRecalculationTests(unittest.TestCase):
    """SP 1.84: United States post-correction recalculation."""

    def test_us_recalculates_factors_and_equity_after_correction(self) -> None:
        result = recalculate(
            MarketTarget.US,
            "AAPL",
            _WEEK,
            _CLOSES,
            adjustment_events=[
                AdjustmentEvent(date(2026, 1, 7), CorporateActionType.SPLIT, ActionTerms(ratio=2.0))
            ],
            entitlement_events=[
                EntitlementEvent(
                    "AAPL-split-1",
                    CorporateActionType.SPLIT,
                    terms=ActionTerms(ratio=2.0),
                    record_date=date(2026, 1, 7),
                ),
                EntitlementEvent(
                    "AAPL-div-1",
                    CorporateActionType.DIVIDEND,
                    terms=ActionTerms(price=1.0),
                    record_date=date(2026, 1, 7),
                ),
            ],
            position_date=date(2026, 1, 5),
            quantity=100.0,
        )

        self.assertTrue(result["recalculated"])
        factors = result["adjusted_factors"]
        self.assertEqual(len(factors), len(_WEEK))
        self.assertAlmostEqual(factors[0]["cumulative_factor"], 0.5)
        self.assertAlmostEqual(factors[2]["daily_factor"], 0.5)

        equity = {row["action_id"]: row for row in result["equity_events"]}
        self.assertEqual(len(equity), 2)
        self.assertEqual(equity["AAPL-split-1"]["entitled_quantity"], 200.0)
        self.assertEqual(equity["AAPL-div-1"]["cash_amount"], 100.0)

    def test_us_rejects_unsupported_event_after_correction(self) -> None:
        result = recalculate(
            MarketTarget.US,
            "AAPL",
            _WEEK,
            _CLOSES,
            adjustment_events=[],
            entitlement_events=[
                EntitlementEvent(
                    "AAPL-rights-1",
                    CorporateActionType.RIGHTS_ISSUE,
                    terms=ActionTerms(ratio=0.5, price=90.0),
                    record_date=date(2026, 1, 7),
                )
            ],
            position_date=date(2026, 1, 5),
            quantity=100.0,
        )
        self.assertFalse(result["recalculated"])
        self.assertEqual(result["review_items"][0]["reason"], "action_type_not_supported")

    def test_us_without_equity_input_skips_equity(self) -> None:
        result = recalculate(
            MarketTarget.US,
            "AAPL",
            _WEEK,
            _CLOSES,
            adjustment_events=[
                AdjustmentEvent(date(2026, 1, 7), CorporateActionType.SPLIT, ActionTerms(ratio=2.0))
            ],
            entitlement_events=[],
        )
        self.assertTrue(result["recalculated"])
        self.assertEqual(result["equity_events"], [])
