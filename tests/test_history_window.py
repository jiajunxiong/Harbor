"""History window computation tool tests (MVP 2 / SP 2.16).

Verifies rolling-window extraction, missing-value handling and minimum
observation gates, and that windows only use data knowable on or before the
decision date (no future-dated quotes can enter a window).
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.history_window import (
    WindowConfig,
    consecutive_returns,
    extract_price_window,
    has_min_observations,
    max_drawdown,
    observation_count,
    safe_mean,
    safe_std,
    safe_sum,
    window_closes,
)

_SYMBOL = "0005.HK"


def _quote(day: date, close: float) -> DailyQuote:
    return DailyQuote(Market.HK, _SYMBOL, day, close, close, close, close, 1_000_000, close)


class WindowConfigTests(unittest.TestCase):
    """Verify the window configuration validation (SP 2.16)."""

    def test_defaults(self) -> None:
        config = WindowConfig()
        self.assertEqual(config.lookback_days, 252)
        self.assertEqual(config.min_observations, 60)

    def test_rejects_negative_lookback(self) -> None:
        with self.assertRaisesRegex(ValueError, "lookback"):
            WindowConfig(lookback_days=-1)

    def test_rejects_negative_min_observations(self) -> None:
        with self.assertRaisesRegex(ValueError, "min_observations"):
            WindowConfig(min_observations=-1)


class WindowClosesTests(unittest.TestCase):
    """Verify price-window extraction uses only decision-date-or-earlier data."""

    def test_excludes_future_and_old_quotes(self) -> None:
        quotes = [
            _quote(date(2026, 2, 1), 90.0),
            _quote(date(2026, 3, 1), 99.0),
            _quote(date(2026, 3, 30), 102.0),
            _quote(date(2026, 4, 1), 105.0),
        ]
        closes = window_closes(quotes, date(2026, 3, 31), 30)
        self.assertEqual(closes, (99.0, 102.0))

    def test_sorts_ascending(self) -> None:
        quotes = [
            _quote(date(2026, 3, 30), 102.0),
            _quote(date(2026, 3, 1), 99.0),
        ]
        self.assertEqual(window_closes(quotes, date(2026, 3, 31), 365), (99.0, 102.0))

    def test_empty_window_when_nothing_in_range(self) -> None:
        quotes = [_quote(date(2025, 1, 1), 90.0)]
        self.assertEqual(window_closes(quotes, date(2026, 3, 31), 30), ())

    def test_zero_lookback_keeps_only_decision_date(self) -> None:
        quotes = [
            _quote(date(2026, 3, 31), 100.0),
            _quote(date(2026, 3, 30), 99.0),
        ]
        self.assertEqual(window_closes(quotes, date(2026, 3, 31), 0), (100.0,))

    def test_rejects_negative_lookback(self) -> None:
        with self.assertRaisesRegex(ValueError, "lookback"):
            window_closes((), date(2026, 3, 31), -1)


class PriceWindowResultTests(unittest.TestCase):
    """Verify the gated price window result (SP 2.16)."""

    def test_sufficient_and_observation_count(self) -> None:
        quotes = [_quote(date(2026, 3, 1), 99.0), _quote(date(2026, 3, 30), 102.0)]
        result = extract_price_window(
            quotes, date(2026, 3, 31), WindowConfig(lookback_days=30, min_observations=2)
        )
        self.assertEqual(result.observation_count, 2)
        self.assertTrue(result.sufficient)
        self.assertEqual(result.closes, (99.0, 102.0))

    def test_insufficient_below_min_observations(self) -> None:
        quotes = [_quote(date(2026, 3, 1), 99.0)]
        result = extract_price_window(
            quotes, date(2026, 3, 31), WindowConfig(lookback_days=30, min_observations=5)
        )
        self.assertEqual(result.observation_count, 1)
        self.assertFalse(result.sufficient)


class MissingValueHelpersTests(unittest.TestCase):
    """Verify missing-value counting and gating (SP 2.16)."""

    def test_observation_count_skips_none(self) -> None:
        self.assertEqual(observation_count([1.0, None, 3.0]), 2)
        self.assertEqual(observation_count([]), 0)

    def test_has_min_observations(self) -> None:
        self.assertTrue(has_min_observations([1.0, None, 3.0], 2))
        self.assertFalse(has_min_observations([1.0, None, 3.0], 3))
        self.assertTrue(has_min_observations([], 0))

    def test_has_min_observations_rejects_negative(self) -> None:
        with self.assertRaisesRegex(ValueError, "min_observations"):
            has_min_observations([], -1)

    def test_safe_sum_skips_none(self) -> None:
        self.assertEqual(safe_sum([1.0, None, 3.0], 2), 4.0)

    def test_safe_sum_none_below_gate(self) -> None:
        self.assertIsNone(safe_sum([1.0, None, 3.0], 3))

    def test_safe_mean_skips_none(self) -> None:
        self.assertEqual(safe_mean([1.0, None, 3.0], 2), 2.0)

    def test_safe_mean_none_below_gate(self) -> None:
        self.assertIsNone(safe_mean([1.0, None, 3.0], 3))


class SafeStdTests(unittest.TestCase):
    """Verify standard deviation computation with gating (SP 2.16)."""

    def test_sample_std(self) -> None:
        result = safe_std([1.0, 2.0, 3.0, 4.0], 2)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 1.2909944487, places=9)

    def test_population_std(self) -> None:
        result = safe_std([1.0, 2.0, 3.0, 4.0], 2, sample=False)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 1.1180339887, places=9)

    def test_none_below_gate(self) -> None:
        self.assertIsNone(safe_std([1.0, 2.0, 3.0, 4.0], 5))

    def test_none_with_single_observation(self) -> None:
        self.assertIsNone(safe_std([1.0], 1))

    def test_skips_none_values(self) -> None:
        result = safe_std([1.0, None, 2.0, 3.0, 4.0], 2)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 1.2909944487, places=9)


class ConsecutiveReturnsTests(unittest.TestCase):
    """Verify consecutive-return computation (SP 2.16)."""

    def test_returns_between_adjacent_values(self) -> None:
        returns = consecutive_returns([100.0, 110.0, 121.0])
        self.assertEqual(len(returns), 2)
        self.assertAlmostEqual(returns[0], 0.1)
        self.assertAlmostEqual(returns[1], 0.1)

    def test_skips_none(self) -> None:
        returns = consecutive_returns([100.0, None, 121.0])
        self.assertEqual(len(returns), 1)
        self.assertAlmostEqual(returns[0], 0.21)

    def test_empty_when_fewer_than_two(self) -> None:
        self.assertEqual(consecutive_returns([100.0]), ())
        self.assertEqual(consecutive_returns([]), ())

    def test_skips_non_positive_previous(self) -> None:
        self.assertEqual(consecutive_returns([0.0, 100.0, 200.0]), (1.0,))


class MaxDrawdownTests(unittest.TestCase):
    """Verify maximum-drawdown computation (SP 2.16)."""

    def test_tracks_peaks(self) -> None:
        self.assertEqual(max_drawdown([100.0, 120.0, 90.0, 130.0]), -0.25)

    def test_strictly_declining_series(self) -> None:
        self.assertEqual(max_drawdown([100.0, 80.0, 60.0, 70.0]), -0.4)

    def test_monotonic_series_has_no_drawdown(self) -> None:
        self.assertEqual(max_drawdown([100.0, 110.0, 120.0]), 0.0)

    def test_none_with_fewer_than_two(self) -> None:
        self.assertIsNone(max_drawdown([100.0]))
        self.assertIsNone(max_drawdown([]))

    def test_skips_none_values(self) -> None:
        result = max_drawdown([100.0, None, 90.0])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, -0.1)


if __name__ == "__main__":
    unittest.main()
