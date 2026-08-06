"""Rebalance-date generation tests (MVP 2 / SP 2.33).

Verifies quarter-start, quarter-end and specified-date anchors, forward and
backward deferral on non-trading days, independent HK/US deferral, and that
generation is deterministic and replayable.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.rebalance_schedule import (
    DeferralRule,
    RebalanceAnchor,
    RebalanceSchedule,
    generate_rebalance_days,
)
from harbor.core.trading_calendar import MarketTradingCalendar

_START = date(2026, 1, 1)
_END = date(2026, 12, 31)


def _calendar(
    hk_holidays: frozenset[date] = frozenset(),
    us_holidays: frozenset[date] = frozenset(),
) -> MarketTradingCalendar:
    """Return a calendar with the given market holidays (weekdays only)."""
    return MarketTradingCalendar({Market.HK: hk_holidays, Market.US: us_holidays})


class QuarterStartTests(unittest.TestCase):
    """Verify quarter-start anchors (季度首)."""

    def test_quarter_starts_when_trading(self) -> None:
        days = generate_rebalance_days(
            Market.HK,
            _START,
            _END,
            _calendar(),
            RebalanceSchedule(anchor=RebalanceAnchor.QUARTER_START),
        )
        self.assertEqual(
            days,
            (date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1), date(2026, 10, 1)),
        )

    def test_quarter_start_deferred_forward_on_holiday(self) -> None:
        calendar = _calendar(hk_holidays=frozenset({date(2026, 4, 1)}))
        days = generate_rebalance_days(
            Market.HK,
            _START,
            _END,
            calendar,
            RebalanceSchedule(anchor=RebalanceAnchor.QUARTER_START),
        )
        self.assertEqual(days[1], date(2026, 4, 2))  # Apr 1 holiday -> Apr 2
        self.assertEqual(
            days,
            (date(2026, 1, 1), date(2026, 4, 2), date(2026, 7, 1), date(2026, 10, 1)),
        )

    def test_default_schedule_matches_calendar_contract(self) -> None:
        calendar = _calendar()
        generated = generate_rebalance_days(
            Market.HK,
            _START,
            _END,
            calendar,
            RebalanceSchedule(),
        )
        self.assertEqual(generated, tuple(calendar.rebalance_days(Market.HK, _START, _END)))


class QuarterEndTests(unittest.TestCase):
    """Verify quarter-end anchors (季度末)."""

    def test_quarter_ends_forward(self) -> None:
        days = generate_rebalance_days(
            Market.HK,
            _START,
            _END,
            _calendar(),
            RebalanceSchedule(anchor=RebalanceAnchor.QUARTER_END),
        )
        self.assertEqual(
            days,
            (date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30), date(2026, 12, 31)),
        )

    def test_quarter_end_backward_on_holiday(self) -> None:
        calendar = _calendar(hk_holidays=frozenset({date(2026, 3, 31)}))
        days = generate_rebalance_days(
            Market.HK,
            _START,
            _END,
            calendar,
            RebalanceSchedule(
                anchor=RebalanceAnchor.QUARTER_END,
                deferral=DeferralRule.BACKWARD,
            ),
        )
        self.assertEqual(days[0], date(2026, 3, 30))  # Mar 31 holiday -> Mar 30

    def test_forward_vs_backward_deferral_differ(self) -> None:
        calendar = _calendar(hk_holidays=frozenset({date(2026, 3, 31)}))
        forward = generate_rebalance_days(
            Market.HK,
            _START,
            _END,
            calendar,
            RebalanceSchedule(
                anchor=RebalanceAnchor.QUARTER_END,
                deferral=DeferralRule.FORWARD,
            ),
        )
        backward = generate_rebalance_days(
            Market.HK,
            _START,
            _END,
            calendar,
            RebalanceSchedule(
                anchor=RebalanceAnchor.QUARTER_END,
                deferral=DeferralRule.BACKWARD,
            ),
        )
        self.assertNotEqual(forward, backward)
        self.assertEqual(forward[0], date(2026, 4, 1))
        self.assertEqual(backward[0], date(2026, 3, 30))


class SpecifiedDateTests(unittest.TestCase):
    """Verify specified-date anchors (指定日期)."""

    def test_specified_dates_deferred_forward(self) -> None:
        days = generate_rebalance_days(
            Market.HK,
            _START,
            _END,
            _calendar(),
            RebalanceSchedule(
                anchor=RebalanceAnchor.SPECIFIED,
                specified_dates=(
                    date(2026, 2, 14),  # Saturday -> Monday Feb 16
                    date(2026, 3, 31),  # Tuesday (trading)
                ),
            ),
        )
        self.assertEqual(days, (date(2026, 2, 16), date(2026, 3, 31)))

    def test_specified_dates_outside_range_dropped(self) -> None:
        days = generate_rebalance_days(
            Market.HK,
            date(2026, 3, 1),
            date(2026, 6, 30),
            _calendar(),
            RebalanceSchedule(
                anchor=RebalanceAnchor.SPECIFIED,
                specified_dates=(date(2025, 12, 31), date(2026, 4, 1), date(2027, 1, 1)),
            ),
        )
        self.assertEqual(days, (date(2026, 4, 1),))

    def test_specified_dates_deduplicated(self) -> None:
        days = generate_rebalance_days(
            Market.HK,
            _START,
            _END,
            _calendar(),
            RebalanceSchedule(
                anchor=RebalanceAnchor.SPECIFIED,
                specified_dates=(date(2026, 3, 28), date(2026, 3, 30)),
            ),
        )
        # Mar 28 (Sat) and Mar 30 (Mon) both defer to Mar 30.
        self.assertEqual(days, (date(2026, 3, 30),))


class MarketIndependenceTests(unittest.TestCase):
    """Verify HK and US defer independently (SP 2.33)."""

    def test_markets_defer_with_their_own_holidays(self) -> None:
        calendar = _calendar(
            us_holidays=frozenset({date(2026, 4, 1)}),
        )
        schedule = RebalanceSchedule(anchor=RebalanceAnchor.QUARTER_START)
        hk = generate_rebalance_days(Market.HK, _START, _END, calendar, schedule)
        us = generate_rebalance_days(Market.US, _START, _END, calendar, schedule)
        self.assertEqual(hk[1], date(2026, 4, 1))  # HK trades Apr 1
        self.assertEqual(us[1], date(2026, 4, 2))  # US defers to Apr 2


class ValidationAndDeterminismTests(unittest.TestCase):
    """Verify schedule validation and deterministic output."""

    def test_rejects_empty_specified_dates(self) -> None:
        with self.assertRaisesRegex(ValueError, "specified"):
            RebalanceSchedule(
                anchor=RebalanceAnchor.SPECIFIED,
                specified_dates=(),
            )

    def test_rejects_specified_dates_for_non_specified_anchor(self) -> None:
        with self.assertRaisesRegex(ValueError, "only apply"):
            RebalanceSchedule(
                anchor=RebalanceAnchor.QUARTER_START,
                specified_dates=(date(2026, 1, 1),),
            )

    def test_rejects_duplicate_specified_dates(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicates"):
            RebalanceSchedule(
                anchor=RebalanceAnchor.SPECIFIED,
                specified_dates=(date(2026, 1, 1), date(2026, 1, 1)),
            )

    def test_rejects_unsorted_specified_dates(self) -> None:
        with self.assertRaisesRegex(ValueError, "sorted ascending"):
            RebalanceSchedule(
                anchor=RebalanceAnchor.SPECIFIED,
                specified_dates=(date(2026, 2, 1), date(2026, 1, 1)),
            )

    def test_rejects_end_before_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "end on or after start"):
            generate_rebalance_days(
                Market.HK,
                date(2026, 4, 1),
                date(2026, 1, 1),
                _calendar(),
                RebalanceSchedule(),
            )

    def test_empty_range_yields_no_days(self) -> None:
        days = generate_rebalance_days(
            Market.HK,
            date(2026, 4, 15),
            date(2026, 4, 20),
            _calendar(),
            RebalanceSchedule(),
        )
        self.assertEqual(days, ())

    def test_repeat_generation_identical(self) -> None:
        calendar = _calendar(hk_holidays=frozenset({date(2026, 4, 1)}))
        schedule = RebalanceSchedule(anchor=RebalanceAnchor.QUARTER_START)
        first = generate_rebalance_days(Market.HK, _START, _END, calendar, schedule)
        second = generate_rebalance_days(Market.HK, _START, _END, calendar, schedule)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
