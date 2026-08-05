"""Point-in-time availability rule tests (MVP 2 / SP 2.9)."""

import unittest
from datetime import date

from harbor.core.adjustments import ActionTerms
from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import (
    AdjustmentFactor,
    DailyQuote,
    Dividend,
    FundamentalRecord,
)
from harbor.core.equity import EntitlementEvent
from harbor.core.market_registry import CorporateActionType
from harbor.core.point_in_time import (
    PointInTimeError,
    available_as_of,
    available_on,
    filter_available,
    require_available,
)


def _fundamental(available_on: date | None) -> FundamentalRecord:
    return FundamentalRecord(
        market=Market.US,
        symbol="AAPL",
        report_date=date(2025, 12, 31),
        fiscal_period="FY2025",
        available_on=available_on,
        roe=0.3,
    )


def _quote(day: date) -> DailyQuote:
    return DailyQuote(
        market=Market.US,
        symbol="AAPL",
        day=day,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=100,
        adjusted_close=1.0,
    )


def _dividend(ex_date: date) -> Dividend:
    return Dividend(
        market=Market.HK,
        symbol="0005.HK",
        amount=1.0,
        currency=Currency.HKD,
        ex_date=ex_date,
    )


def _factor(day: date) -> AdjustmentFactor:
    return AdjustmentFactor(
        market=Market.US,
        symbol="AAPL",
        date=day,
        cumulative_factor=1.0,
        daily_factor=1.0,
    )


def _event(ex_date: date | None) -> EntitlementEvent:
    return EntitlementEvent(
        action_id="a-1",
        action_type=CorporateActionType.SPLIT,
        terms=ActionTerms(ratio=2.0),
        ex_date=ex_date,
    )


class AvailableOnTests(unittest.TestCase):
    """Verify the availability-date rule for each record type."""

    def test_fundamental_uses_explicit_disclosure_date(self) -> None:
        self.assertEqual(available_on(_fundamental(date(2026, 3, 1))), date(2026, 3, 1))
        self.assertIsNone(available_on(_fundamental(None)))

    def test_quote_available_on_its_day(self) -> None:
        self.assertEqual(available_on(_quote(date(2026, 1, 2))), date(2026, 1, 2))

    def test_dividend_available_on_ex_date(self) -> None:
        self.assertEqual(available_on(_dividend(date(2026, 3, 1))), date(2026, 3, 1))

    def test_adjustment_factor_available_on_its_date(self) -> None:
        self.assertEqual(available_on(_factor(date(2026, 1, 2))), date(2026, 1, 2))

    def test_entitlement_event_available_on_ex_date(self) -> None:
        self.assertEqual(available_on(_event(date(2026, 1, 5))), date(2026, 1, 5))
        self.assertIsNone(available_on(_event(None)))

    def test_unsupported_type_raises(self) -> None:
        with self.assertRaisesRegex(TypeError, "No point-in-time rule"):
            available_on("not-a-record")


class AvailabilityBoundaryTests(unittest.TestCase):
    """Verify the knowable-on-or-before boundary."""

    def test_available_on_or_before_as_of(self) -> None:
        as_of = date(2026, 3, 31)
        self.assertTrue(available_as_of(_fundamental(date(2026, 3, 31)), as_of))
        self.assertFalse(available_as_of(_fundamental(date(2026, 4, 1)), as_of))
        self.assertFalse(available_as_of(_fundamental(None), as_of))

    def test_filter_available_drops_unknown_and_future(self) -> None:
        as_of = date(2026, 3, 31)
        kept = filter_available(
            [
                _fundamental(date(2026, 3, 1)),
                _fundamental(date(2026, 4, 1)),
                _fundamental(None),
            ],
            as_of,
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].available_on, date(2026, 3, 1))


class RequireAvailableTests(unittest.TestCase):
    """Verify the strict refusal path."""

    def test_require_available_passes_for_timely_record(self) -> None:
        require_available(_fundamental(date(2026, 3, 1)), date(2026, 3, 31))

    def test_require_available_refuses_unknown_availability(self) -> None:
        with self.assertRaisesRegex(PointInTimeError, "no known availability date"):
            require_available(_fundamental(None), date(2026, 3, 31))

    def test_require_available_refuses_future_dated_record(self) -> None:
        with self.assertRaisesRegex(PointInTimeError, "was not knowable on"):
            require_available(_fundamental(date(2026, 4, 1)), date(2026, 3, 31))


if __name__ == "__main__":
    unittest.main()
