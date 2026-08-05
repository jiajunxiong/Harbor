"""Annualized volatility factor tests (MVP 2 / SP 2.19).

Verifies windowed daily-return volatility, per-market annualization, the
minimum-observation gate, zero volatility for flat prices, and that
future-dated quotes can never enter the window.
"""

import unittest
from collections.abc import Sequence
from datetime import date, timedelta

from harbor.core.backtest_domain import Market
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.factor_volatility import (
    VolatilityConfig,
    annualize_volatility,
    annualized_volatility_factor,
)
from harbor.core.history_window import WindowConfig

_SYMBOL = "0005.HK"
_DECISION = date(2025, 12, 31)


def _quote(day: date, close: float) -> DailyQuote:
    return DailyQuote(Market.HK, _SYMBOL, day, close, close, close, close, 1_000_000, close)


def _quotes_from_returns(
    returns: Sequence[float],
    start: date = date(2025, 1, 1),
) -> tuple[DailyQuote, ...]:
    """Build daily quotes whose consecutive returns equal ``returns``."""
    closes = [100.0]
    for rate in returns:
        closes.append(closes[-1] * (1.0 + rate))
    return tuple(_quote(start + timedelta(days=index), close) for index, close in enumerate(closes))


def _config(annual_trading_days: int = 252, min_observations: int = 60) -> VolatilityConfig:
    return VolatilityConfig(
        window=WindowConfig(lookback_days=365, min_observations=min_observations),
        annual_trading_days=annual_trading_days,
    )


class VolatilityConfigTests(unittest.TestCase):
    """Verify factor configuration validation (SP 2.19)."""

    def test_rejects_non_positive_annual_trading_days(self) -> None:
        with self.assertRaisesRegex(ValueError, "annual_trading_days"):
            VolatilityConfig(annual_trading_days=0)
        with self.assertRaisesRegex(ValueError, "annual_trading_days"):
            VolatilityConfig(annual_trading_days=-1)


class AnnualizeVolatilityTests(unittest.TestCase):
    """Verify annualization scaling (SP 2.19)."""

    def test_scales_by_sqrt_of_trading_days(self) -> None:
        result = annualize_volatility(0.01, 252)
        self.assertAlmostEqual(result, 0.01 * (252**0.5), places=12)

    def test_rejects_non_positive_trading_days(self) -> None:
        with self.assertRaisesRegex(ValueError, "annual_trading_days"):
            annualize_volatility(0.01, 0)


class AnnualizedVolatilityFactorTests(unittest.TestCase):
    """Verify the annualized volatility factor (SP 2.19)."""

    def test_alternating_returns_yields_expected_volatility(self) -> None:
        quotes = _quotes_from_returns([0.01, -0.01] * 126)
        result = annualized_volatility_factor(quotes, _DECISION, config=_config())
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.daily_volatility, 0.01, places=6)
        self.assertAlmostEqual(result.value, 0.01 * (252**0.5), places=6)
        self.assertEqual(result.returns_count, 252)
        self.assertEqual(result.observation_count, 253)

    def test_per_market_annualization(self) -> None:
        quotes = _quotes_from_returns([0.01, -0.01] * 126)
        hk = annualized_volatility_factor(
            quotes, _DECISION, config=_config(annual_trading_days=247)
        )
        us = annualized_volatility_factor(
            quotes, _DECISION, config=_config(annual_trading_days=252)
        )
        self.assertIsNotNone(hk.value)
        self.assertIsNotNone(us.value)
        self.assertAlmostEqual(hk.value, 0.01 * (247**0.5), places=6)
        self.assertAlmostEqual(us.value, 0.01 * (252**0.5), places=6)
        self.assertNotEqual(hk.value, us.value)

    def test_insufficient_observations_yields_none(self) -> None:
        quotes = _quotes_from_returns([0.01] * 4)
        result = annualized_volatility_factor(
            quotes, _DECISION, config=_config(min_observations=60)
        )
        self.assertIsNone(result.value)
        self.assertIsNone(result.daily_volatility)
        self.assertEqual(result.observation_count, 5)

    def test_single_return_yields_none(self) -> None:
        quotes = _quotes_from_returns([0.01])
        result = annualized_volatility_factor(quotes, _DECISION, config=_config(min_observations=1))
        self.assertIsNone(result.value)

    def test_flat_price_yields_zero_volatility(self) -> None:
        quotes = tuple(_quote(date(2025, 1, 1) + timedelta(days=i), 100.0) for i in range(253))
        result = annualized_volatility_factor(quotes, _DECISION, config=_config())
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.daily_volatility, 0.0)

    def test_future_quotes_excluded(self) -> None:
        base = _quotes_from_returns([0.01, -0.01] * 126)
        with_future = base + (_quote(_DECISION + timedelta(days=1), 10_000.0),)
        expected = annualized_volatility_factor(base, _DECISION, config=_config())
        actual = annualized_volatility_factor(with_future, _DECISION, config=_config())
        self.assertIsNotNone(expected.value)
        self.assertAlmostEqual(actual.value, expected.value, places=9)

    def test_lookback_window_excludes_old_quotes(self) -> None:
        quotes = _quotes_from_returns([0.5, -0.3] + [0.01, -0.01] * 120)
        wide = annualized_volatility_factor(quotes, _DECISION, config=_config())
        narrow = annualized_volatility_factor(
            quotes,
            _DECISION,
            config=VolatilityConfig(
                window=WindowConfig(lookback_days=200, min_observations=60),
                annual_trading_days=252,
            ),
        )
        # The 200-day lookback excludes the huge early returns (Jan 2025) while
        # still holding enough observations to be sufficient.
        self.assertIsNotNone(wide.value)
        self.assertIsNotNone(narrow.value)
        self.assertNotAlmostEqual(narrow.value, wide.value, places=6)

    def test_configurable_min_observations(self) -> None:
        quotes = _quotes_from_returns([0.01, -0.01] * 126)
        strict = annualized_volatility_factor(
            quotes, _DECISION, config=_config(min_observations=400)
        )
        self.assertIsNone(strict.value)


if __name__ == "__main__":
    unittest.main()
