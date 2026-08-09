"""Trading calendar tests (MVP 2 / SP 2.11)."""

import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.backtest_interfaces import TradingCalendar
from harbor.core.trading_calendar import (
    DEFAULT_HOLIDAYS,
    MarketTradingCalendar,
    _quarter_starts,
)

_HK_HOLIDAYS = frozenset({date(2026, 1, 1), date(2026, 4, 6)})
_US_HOLIDAYS = frozenset({date(2026, 7, 3)})
_HOLIDAYS = {Market.HK: _HK_HOLIDAYS, Market.US: _US_HOLIDAYS}


class CalendarContractTests(unittest.TestCase):
    """Verify the calendar satisfies the SP 2.3 interface."""

    def test_calendar_implements_trading_calendar_interface(self) -> None:
        self.assertTrue(issubclass(MarketTradingCalendar, TradingCalendar))


class WeekdayTests(unittest.TestCase):
    """Weekends are not trading days; weekdays are (absent holidays)."""

    def test_weekend_is_not_a_trading_day(self) -> None:
        calendar = MarketTradingCalendar({})
        self.assertFalse(calendar.is_trading_day(Market.HK, date(2026, 1, 3)))  # Saturday
        self.assertFalse(calendar.is_trading_day(Market.HK, date(2026, 1, 4)))  # Sunday

    def test_plain_weekday_is_a_trading_day(self) -> None:
        calendar = MarketTradingCalendar({})
        self.assertTrue(calendar.is_trading_day(Market.HK, date(2026, 1, 5)))  # Monday


class HolidayTests(unittest.TestCase):
    """Market holidays are non-trading days, independently per market."""

    def test_holiday_is_not_a_trading_day(self) -> None:
        calendar = MarketTradingCalendar(_HOLIDAYS)
        self.assertFalse(calendar.is_trading_day(Market.HK, date(2026, 1, 1)))

    def test_markets_keep_independent_calendars(self) -> None:
        calendar = MarketTradingCalendar(_HOLIDAYS)
        # 2026-01-01 is a holiday in HK but a normal Thursday in US.
        self.assertFalse(calendar.is_trading_day(Market.HK, date(2026, 1, 1)))
        self.assertTrue(calendar.is_trading_day(Market.US, date(2026, 1, 1)))
        # 2026-07-03 is a holiday in US but a normal Friday in HK.
        self.assertTrue(calendar.is_trading_day(Market.HK, date(2026, 7, 3)))
        self.assertFalse(calendar.is_trading_day(Market.US, date(2026, 7, 3)))


class NextPreviousTests(unittest.TestCase):
    """Verify deferral to the next/previous trading day."""

    def test_next_trading_day_skips_weekend(self) -> None:
        calendar = MarketTradingCalendar({})
        self.assertEqual(calendar.next_trading_day(Market.HK, date(2026, 1, 3)), date(2026, 1, 5))

    def test_next_trading_day_skips_holiday(self) -> None:
        calendar = MarketTradingCalendar(_HOLIDAYS)
        self.assertEqual(calendar.next_trading_day(Market.HK, date(2026, 1, 1)), date(2026, 1, 2))

    def test_next_trading_day_returns_same_day_when_trading(self) -> None:
        calendar = MarketTradingCalendar(_HOLIDAYS)
        self.assertEqual(calendar.next_trading_day(Market.HK, date(2026, 1, 2)), date(2026, 1, 2))

    def test_previous_trading_day_skips_weekend(self) -> None:
        calendar = MarketTradingCalendar({})
        self.assertEqual(
            calendar.previous_trading_day(Market.HK, date(2026, 1, 3)), date(2026, 1, 2)
        )

    def test_previous_trading_day_returns_same_day_when_trading(self) -> None:
        calendar = MarketTradingCalendar({})
        self.assertEqual(
            calendar.previous_trading_day(Market.HK, date(2026, 1, 5)), date(2026, 1, 5)
        )


class TradingDaysTests(unittest.TestCase):
    """Verify the inclusive trading-day range excludes closures."""

    def test_trading_days_excludes_weekends_and_holidays(self) -> None:
        calendar = MarketTradingCalendar(_HOLIDAYS)
        days = calendar.trading_days(Market.HK, date(2025, 12, 29), date(2026, 1, 5))
        self.assertEqual(
            days,
            [
                date(2025, 12, 29),
                date(2025, 12, 30),
                date(2025, 12, 31),
                date(2026, 1, 2),  # Jan 1 is a holiday
                date(2026, 1, 5),
            ],
        )

    def test_trading_days_requires_end_after_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "end on or after start"):
            MarketTradingCalendar({}).trading_days(Market.HK, date(2026, 1, 5), date(2026, 1, 1))


class RebalanceDaysTests(unittest.TestCase):
    """Verify quarterly rebalance days defer to the next trading day."""

    def test_rebalance_days_are_first_trading_day_of_quarter(self) -> None:
        calendar = MarketTradingCalendar(_HOLIDAYS)
        days = calendar.rebalance_days(Market.HK, date(2026, 1, 1), date(2026, 6, 30))
        self.assertEqual(days, [date(2026, 1, 2), date(2026, 4, 1)])

    def test_rebalance_days_requires_end_after_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "end on or after start"):
            MarketTradingCalendar({}).rebalance_days(Market.HK, date(2026, 4, 1), date(2026, 1, 1))

    def test_quarter_starts_span_inclusive_range(self) -> None:
        self.assertEqual(
            list(_quarter_starts(date(2026, 1, 1), date(2026, 12, 31))),
            [date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1), date(2026, 10, 1)],
        )


class DefaultHolidayTests(unittest.TestCase):
    """Verify the illustrative default holiday calendar."""

    def test_default_holidays_cover_both_markets(self) -> None:
        self.assertEqual(set(DEFAULT_HOLIDAYS), {Market.HK, Market.US})

    def test_default_calendar_observes_holidays_and_weekdays(self) -> None:
        calendar = MarketTradingCalendar()
        self.assertFalse(calendar.is_trading_day(Market.HK, date(2026, 1, 1)))
        self.assertTrue(calendar.is_trading_day(Market.US, date(2026, 2, 9)))
        self.assertFalse(calendar.is_trading_day(Market.US, date(2026, 7, 3)))

    def test_us_default_covers_example_range_new_years_days(self) -> None:
        """Every Jan 1 in the shipped example range is a US non-trading day, and
        the deferred quarter-start rebalance lands on the first real trading day
        (SP 2.90 regression): a real-data backtest over 2019-2024 must not
        rebalance onto a NYSE holiday with no price (SP 2.36)."""
        calendar = MarketTradingCalendar()
        expected_first_rebalance = {
            2019: date(2019, 1, 2),
            2020: date(2020, 1, 2),
            2021: date(2021, 1, 4),
            2022: date(2022, 1, 3),
            2023: date(2023, 1, 3),
            2024: date(2024, 1, 2),
        }
        for year, expected in expected_first_rebalance.items():
            with self.subTest(year=year):
                self.assertFalse(calendar.is_trading_day(Market.US, date(year, 1, 1)))
                self.assertEqual(calendar.next_trading_day(Market.US, date(year, 1, 1)), expected)

    def test_us_default_treats_quarter_starts_as_trading(self) -> None:
        """Non-holiday US quarter starts are trading days in the example range."""
        calendar = MarketTradingCalendar()
        for day in (
            date(2019, 4, 1),
            date(2020, 7, 1),
            date(2021, 10, 1),
            date(2022, 7, 1),
            date(2024, 4, 1),
        ):
            with self.subTest(day=day):
                self.assertTrue(calendar.is_trading_day(Market.US, day))

    def test_hk_default_covers_example_range_new_years_days(self) -> None:
        """Every Jan 1 in the shipped example range is a HKEX non-trading day,
        and the deferred quarter-start rebalance lands on the first real trading
        day (SP 2.90 parity regression): a real-data HK backtest over 2019-2024
        must not rebalance onto a HKEX holiday with no price (SP 2.36)."""
        calendar = MarketTradingCalendar()
        expected_first_rebalance = {
            2019: date(2019, 1, 2),
            2020: date(2020, 1, 2),
            2021: date(2021, 1, 4),
            2022: date(2022, 1, 3),
            2023: date(2023, 1, 3),
            2024: date(2024, 1, 2),
        }
        for year, expected in expected_first_rebalance.items():
            with self.subTest(year=year):
                self.assertFalse(calendar.is_trading_day(Market.HK, date(year, 1, 1)))
                self.assertEqual(calendar.next_trading_day(Market.HK, date(year, 1, 1)), expected)

    def test_hk_default_treats_quarter_starts_as_trading(self) -> None:
        """Non-holiday HK quarter starts are trading days in the example range.

        Jul 1 (HKSAR Day) and Oct 1 (National Day) are holidays every year, so
        April quarter starts are the representative non-holiday anchors.
        """
        calendar = MarketTradingCalendar()
        for day in (
            date(2019, 4, 1),
            date(2020, 4, 1),
            date(2021, 4, 1),
            date(2022, 4, 1),
        ):
            with self.subTest(day=day):
                self.assertTrue(calendar.is_trading_day(Market.HK, day))


if __name__ == "__main__":
    unittest.main()
