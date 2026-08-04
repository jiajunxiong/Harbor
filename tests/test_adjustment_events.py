"""Per-event-type adjusted factor unit tests (SP 1.105 HK, 1.106 US).

Each corporate action type is tested individually through
``compute_adjustment_factors``: the daily factor applied on the event's ex-date
and the cumulative factor before/after the event.
"""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.adjustments import (
    ActionTerms,
    AdjustmentEvent,
    compute_adjustment_factors,
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
_EVENT_DATE = date(2026, 1, 7)


def _rows(market: MarketTarget, symbol: str, event: AdjustmentEvent) -> list[dict[str, object]]:
    return compute_adjustment_factors(market, symbol, _WEEK, _CLOSES, [event])


def _assert_event(
    test_case: unittest.TestCase,
    rows: list[dict[str, object]],
    expected_daily: float,
    expected_before: float,
) -> None:
    """Assert the daily factor on the event date and cumulative behavior."""
    for index, row in enumerate(rows):
        if index < 2:
            test_case.assertAlmostEqual(float(row["cumulative_factor"]), expected_before)
        else:
            test_case.assertAlmostEqual(float(row["cumulative_factor"]), 1.0)
    test_case.assertAlmostEqual(float(rows[2]["daily_factor"]), expected_daily)


class HkAdjustedFactorEventTests(unittest.TestCase):
    """SP 1.105: Hong Kong per-event-type adjusted factors."""

    def test_hk_rights_issue_factor(self) -> None:
        event = AdjustmentEvent(
            _EVENT_DATE,
            CorporateActionType.RIGHTS_ISSUE,
            ActionTerms(ratio=0.5, price=90.0),
        )
        rows = _rows(MarketTarget.HK, "0700.HK", event)
        factor = (101.0 + 90.0 * 0.5) / (101.0 * 1.5)
        _assert_event(self, rows, factor, factor)

    def test_hk_consolidation_factor(self) -> None:
        event = AdjustmentEvent(
            _EVENT_DATE, CorporateActionType.CONSOLIDATION, ActionTerms(ratio=0.2)
        )
        rows = _rows(MarketTarget.HK, "0005.HK", event)
        _assert_event(self, rows, 5.0, 5.0)

    def test_hk_dividend_factor(self) -> None:
        event = AdjustmentEvent(_EVENT_DATE, CorporateActionType.DIVIDEND, ActionTerms(price=2.0))
        rows = _rows(MarketTarget.HK, "0001.HK", event)
        factor = 99.0 / 101.0
        _assert_event(self, rows, factor, factor)

    def test_hk_tender_offer_does_not_adjust_prices(self) -> None:
        event = AdjustmentEvent(_EVENT_DATE, CorporateActionType.TENDER_OFFER, ActionTerms())
        rows = _rows(MarketTarget.HK, "0011.HK", event)
        for row in rows:
            self.assertEqual(float(row["daily_factor"]), 1.0)
            self.assertEqual(float(row["cumulative_factor"]), 1.0)


class UsAdjustedFactorEventTests(unittest.TestCase):
    """SP 1.106: United States per-event-type adjusted factors."""

    def test_us_split_factor(self) -> None:
        event = AdjustmentEvent(_EVENT_DATE, CorporateActionType.SPLIT, ActionTerms(ratio=2.0))
        rows = _rows(MarketTarget.US, "AAPL", event)
        _assert_event(self, rows, 0.5, 0.5)

    def test_us_merger_factor(self) -> None:
        event = AdjustmentEvent(_EVENT_DATE, CorporateActionType.MERGER, ActionTerms(ratio=0.5))
        rows = _rows(MarketTarget.US, "TSLA", event)
        _assert_event(self, rows, 2.0, 2.0)

    def test_us_spin_off_factor(self) -> None:
        event = AdjustmentEvent(_EVENT_DATE, CorporateActionType.SPIN_OFF, ActionTerms(ratio=0.1))
        rows = _rows(MarketTarget.US, "MSFT", event)
        _assert_event(self, rows, 1.0 / 1.1, 1.0 / 1.1)

    def test_us_dividend_factor(self) -> None:
        event = AdjustmentEvent(_EVENT_DATE, CorporateActionType.DIVIDEND, ActionTerms(price=1.0))
        rows = _rows(MarketTarget.US, "NVDA", event)
        factor = 100.0 / 101.0
        _assert_event(self, rows, factor, factor)
