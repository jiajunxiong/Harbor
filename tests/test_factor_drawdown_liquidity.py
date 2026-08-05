"""Drawdown and liquidity factor tests (MVP 2 / SP 2.20).

Verifies historical maximum drawdown, average daily turnover and suspension
ratio computation, the minimum-observation gate, and that future-dated quotes
can never enter the window.
"""

import unittest
from datetime import date, timedelta

from harbor.core.backtest_domain import Market
from harbor.core.backtest_interfaces import DailyQuote, TradingCalendar
from harbor.core.factor_drawdown_liquidity import (
    DrawdownLiquidityConfig,
    DrawdownLiquidityResult,
    drawdown_liquidity_factor,
)
from harbor.core.history_window import WindowConfig
from harbor.core.trading_calendar import MarketTradingCalendar

_SYMBOL = "0005.HK"
_DECISION = date(2025, 12, 31)

_WEEKDAY_CALENDAR = MarketTradingCalendar({Market.HK: frozenset(), Market.US: frozenset()})


def _quote(day: date, close: float = 100.0, volume: int = 1_000_000) -> DailyQuote:
    return DailyQuote(Market.HK, _SYMBOL, day, close, close, close, close, volume, close)


def _weekdays(start: date, end: date) -> tuple[date, ...]:
    days: list[date] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return tuple(days)


def _config(min_observations: int = 2) -> DrawdownLiquidityConfig:
    return DrawdownLiquidityConfig(
        window=WindowConfig(lookback_days=30, min_observations=min_observations)
    )


class _NoTradingCalendar(TradingCalendar):
    """A calendar that reports no trading days (for the suspension edge case)."""

    def is_trading_day(self, market: Market, day: date) -> bool:
        return False

    def next_trading_day(self, market: Market, day: date) -> date:
        return day

    def previous_trading_day(self, market: Market, day: date) -> date:
        return day

    def trading_days(self, market: Market, start: date, end: date) -> tuple[date, ...]:
        return ()

    def rebalance_days(self, market: Market, start: date, end: date) -> tuple[date, ...]:
        return ()


class MaxDrawdownTests(unittest.TestCase):
    """Verify historical maximum drawdown (SP 2.20)."""

    def test_known_series_drawdown(self) -> None:
        days = _weekdays(date(2025, 12, 1), date(2025, 12, 4))
        quotes = tuple(
            _quote(day, close)
            for day, close in zip(days, (100.0, 120.0, 90.0, 130.0), strict=False)
        )
        result = drawdown_liquidity_factor(
            Market.HK, quotes, _DECISION, _WEEKDAY_CALENDAR, config=_config()
        )
        self.assertAlmostEqual(result.max_drawdown, -0.25, places=9)

    def test_monotonic_series_has_no_drawdown(self) -> None:
        days = _weekdays(date(2025, 12, 1), date(2025, 12, 4))
        quotes = tuple(
            _quote(day, close)
            for day, close in zip(days, (100.0, 110.0, 120.0, 130.0), strict=False)
        )
        result = drawdown_liquidity_factor(
            Market.HK, quotes, _DECISION, _WEEKDAY_CALENDAR, config=_config()
        )
        self.assertEqual(result.max_drawdown, 0.0)

    def test_insufficient_observations_yields_none(self) -> None:
        quotes = (_quote(date(2025, 12, 1), 100.0),)
        result = drawdown_liquidity_factor(
            Market.HK, quotes, _DECISION, _WEEKDAY_CALENDAR, config=_config(min_observations=60)
        )
        self.assertIsNone(result.max_drawdown)


class AverageTurnoverTests(unittest.TestCase):
    """Verify average daily turnover (SP 2.20)."""

    def test_mean_of_volume_times_close(self) -> None:
        days = _weekdays(date(2025, 12, 1), date(2025, 12, 4))
        quotes = tuple(_quote(day, close=100.0, volume=1_000) for day in days)
        result = drawdown_liquidity_factor(
            Market.HK, quotes, _DECISION, _WEEKDAY_CALENDAR, config=_config()
        )
        self.assertEqual(result.average_turnover, 100_000.0)

    def test_below_min_observations_yields_none(self) -> None:
        quotes = (_quote(date(2025, 12, 1), close=100.0, volume=1_000),)
        result = drawdown_liquidity_factor(
            Market.HK, quotes, _DECISION, _WEEKDAY_CALENDAR, config=_config(min_observations=60)
        )
        self.assertIsNone(result.average_turnover)


class SuspensionRatioTests(unittest.TestCase):
    """Verify suspension ratio against the market calendar (SP 2.20)."""

    def test_full_coverage_yields_zero_suspension(self) -> None:
        days = _weekdays(date(2025, 12, 1), _DECISION)
        quotes = tuple(_quote(day) for day in days)
        result = drawdown_liquidity_factor(
            Market.HK, quotes, _DECISION, _WEEKDAY_CALENDAR, config=_config()
        )
        self.assertEqual(result.expected_days, len(days))
        self.assertEqual(result.observed_days, len(days))
        self.assertEqual(result.suspension_ratio, 0.0)

    def test_partial_coverage_ratio(self) -> None:
        days = _weekdays(date(2025, 12, 1), _DECISION)
        observed = days[::2]
        quotes = tuple(_quote(day) for day in observed)
        result = drawdown_liquidity_factor(
            Market.HK, quotes, _DECISION, _WEEKDAY_CALENDAR, config=_config()
        )
        self.assertIsNotNone(result.suspension_ratio)
        self.assertAlmostEqual(
            result.suspension_ratio,
            1.0 - len(observed) / len(days),
            places=9,
        )

    def test_no_expected_trading_days_yields_none(self) -> None:
        quotes = (_quote(date(2025, 12, 1)),)
        result = drawdown_liquidity_factor(
            Market.HK, quotes, _DECISION, _NoTradingCalendar(), config=_config()
        )
        self.assertEqual(result.expected_days, 0)
        self.assertIsNone(result.suspension_ratio)

    def test_observed_exceeding_expected_is_clamped_to_zero(self) -> None:
        days = _weekdays(date(2025, 12, 1), _DECISION)
        weekend_days = [
            date(2025, 12, 6),
            date(2025, 12, 7),
            date(2025, 12, 13),
            date(2025, 12, 14),
        ]
        quotes = tuple(_quote(day) for day in days + tuple(weekend_days))
        result = drawdown_liquidity_factor(
            Market.HK, quotes, _DECISION, _WEEKDAY_CALENDAR, config=_config()
        )
        self.assertGreater(result.observed_days, result.expected_days)
        self.assertEqual(result.suspension_ratio, 0.0)


class IntegrationTests(unittest.TestCase):
    """Verify point-in-time behavior and result shape (SP 2.20)."""

    def test_future_quotes_excluded(self) -> None:
        days = _weekdays(date(2025, 12, 1), _DECISION)
        quotes = tuple(_quote(day, close=100.0) for day in days)
        with_future = quotes + (_quote(_DECISION + timedelta(days=1), close=1.0),)
        expected = drawdown_liquidity_factor(
            Market.HK, quotes, _DECISION, _WEEKDAY_CALENDAR, config=_config()
        )
        actual = drawdown_liquidity_factor(
            Market.HK, with_future, _DECISION, _WEEKDAY_CALENDAR, config=_config()
        )
        self.assertEqual(actual.max_drawdown, expected.max_drawdown)
        self.assertEqual(actual.average_turnover, expected.average_turnover)
        self.assertEqual(actual.observed_days, expected.observed_days)

    def test_lookback_window_excludes_old_quotes(self) -> None:
        old_peak = _quote(date(2025, 6, 1), close=200.0)
        days = _weekdays(date(2025, 12, 1), _DECISION)
        quotes = (old_peak,) + tuple(_quote(day, close=100.0) for day in days)
        wide = drawdown_liquidity_factor(
            Market.HK,
            quotes,
            _DECISION,
            _WEEKDAY_CALENDAR,
            config=DrawdownLiquidityConfig(
                window=WindowConfig(lookback_days=365, min_observations=2)
            ),
        )
        narrow = drawdown_liquidity_factor(
            Market.HK,
            quotes,
            _DECISION,
            _WEEKDAY_CALENDAR,
            config=DrawdownLiquidityConfig(
                window=WindowConfig(lookback_days=30, min_observations=2)
            ),
        )
        # The June peak falls inside the 365-day window (drawdown to 100) but
        # outside the 30-day window (all closes 100, no drawdown).
        self.assertNotEqual(wide.max_drawdown, narrow.max_drawdown)
        self.assertAlmostEqual(wide.max_drawdown, -0.5, places=9)
        self.assertEqual(narrow.max_drawdown, 0.0)

    def test_result_is_immutable_dataclass(self) -> None:
        result = drawdown_liquidity_factor(
            Market.HK,
            (_quote(date(2025, 12, 1)), _quote(date(2025, 12, 2))),
            _DECISION,
            _WEEKDAY_CALENDAR,
            config=_config(),
        )
        self.assertIsInstance(result, DrawdownLiquidityResult)
        self.assertEqual(result.decision_date, _DECISION)


if __name__ == "__main__":
    unittest.main()
