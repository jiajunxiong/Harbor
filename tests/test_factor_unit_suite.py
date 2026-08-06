"""Consolidated factor unit tests (MVP 2 / SP 2.31).

For each factor from SP 2.17-2.21 the suite covers the acceptance dimensions:

- normal values (正常值): a typical input yields the expected value;
- missing values (缺失值): unavailable inputs yield ``None`` (unassessable)
  with a readable reason, never a fabricated number;
- boundary dates (边界日期): records exactly on the decision date are used,
  records after it are excluded, and the trailing-window edge is inclusive;
- special dividends (特别股息): excluded from dividend numerators by default
  and tracked separately (applies to the dividend factors);
- market differences (市场差异): per-market annualization (volatility) and
  per-market trading calendars (drawdown/liquidity), and per-market dividend
  currencies.

Earnings quality has no dividend or market parameter, so it covers normal,
missing and disclosure-date boundaries only.
"""

import math
import unittest
from collections.abc import Sequence
from datetime import date, timedelta

from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import (
    DailyQuote,
    Dividend,
    FundamentalRecord,
    TradingCalendar,
)
from harbor.core.factor_dividend_sustainability import (
    DividendSustainabilityConfig,
    dividend_sustainability_factor,
)
from harbor.core.factor_dividend_yield import dividend_yield_factor
from harbor.core.factor_drawdown_liquidity import (
    DrawdownLiquidityConfig,
    drawdown_liquidity_factor,
)
from harbor.core.factor_earnings_quality import earnings_quality_factor
from harbor.core.factor_volatility import VolatilityConfig, annualized_volatility_factor
from harbor.core.history_window import WindowConfig

_DECISION_DATE = date(2026, 3, 31)  # a Tuesday


def _quote(
    market: Market,
    day: date,
    close: float,
    volume: int = 1_000_000,
) -> DailyQuote:
    return DailyQuote(
        market,
        "SYM",
        day,
        close,
        close,
        close,
        close,
        volume,
        close,
    )


def _dividend(
    market: Market,
    ex_date: date,
    amount: float = 1.0,
    is_special: bool = False,
) -> Dividend:
    currency = Currency.HKD if market is Market.HK else Currency.USD
    return Dividend(market, "SYM", amount, currency, ex_date, is_special=is_special)


def _fundamental(
    market: Market,
    report_date: date,
    available_on: date | None,
    roe: float = 0.15,
) -> FundamentalRecord:
    return FundamentalRecord(
        market,
        "SYM",
        report_date,
        str(report_date.year),
        available_on,
        roe=roe,
        net_income=1.0e9,
        total_equity=1.0e10,
        revenue=5.0e9,
    )


class _WeekdayCalendar(TradingCalendar):
    """A Mon-Fri calendar with optional per-date holidays."""

    def __init__(self, holidays: frozenset[date] = frozenset()) -> None:
        self._holidays = holidays

    def is_trading_day(self, market: Market, day: date) -> bool:
        return day.weekday() < 5 and day not in self._holidays

    def next_trading_day(self, market: Market, day: date) -> date:
        while not self.is_trading_day(market, day):
            day += timedelta(days=1)
        return day

    def previous_trading_day(self, market: Market, day: date) -> date:
        while not self.is_trading_day(market, day):
            day -= timedelta(days=1)
        return day

    def trading_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        days: list[date] = []
        day = start
        while day <= end:
            if self.is_trading_day(market, day):
                days.append(day)
            day += timedelta(days=1)
        return tuple(days)

    def rebalance_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        return ()


class _EmptyCalendar(_WeekdayCalendar):
    """A calendar with no trading days (for the suspension not-assessable case)."""

    def trading_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        return ()


class DividendYieldFactorUnitTests(unittest.TestCase):
    """Dividend yield factor (SP 2.17) dimensions."""

    def test_normal_regular_dividends_annualized(self) -> None:
        dividends = (
            _dividend(Market.HK, _DECISION_DATE - timedelta(days=90), 1.0),
            _dividend(Market.HK, _DECISION_DATE - timedelta(days=30), 1.0),
        )
        result = dividend_yield_factor(dividends, 100.0, _DECISION_DATE, lookback_days=365)
        self.assertAlmostEqual(result.value, 0.02)  # 2.0 annualized / 100
        self.assertEqual(result.eligible_sum, 2.0)
        self.assertEqual(result.dividend_count, 2)
        self.assertEqual(result.special_sum, 0.0)

    def test_missing_price_makes_yield_unavailable(self) -> None:
        dividends = (_dividend(Market.HK, _DECISION_DATE - timedelta(days=30)),)
        self.assertIsNone(dividend_yield_factor(dividends, None, _DECISION_DATE).value)
        self.assertIsNone(dividend_yield_factor(dividends, 0.0, _DECISION_DATE).value)

    def test_no_eligible_dividends_yields_zero(self) -> None:
        result = dividend_yield_factor((), 100.0, _DECISION_DATE, lookback_days=365)
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.eligible_sum, 0.0)

    def test_boundary_dates_window_is_inclusive_on_decision_date(self) -> None:
        on_edge = _dividend(Market.HK, _DECISION_DATE, 1.0)
        window_start = _DECISION_DATE - timedelta(days=365)
        at_start = _dividend(Market.HK, window_start, 1.0)
        after = _dividend(Market.HK, _DECISION_DATE + timedelta(days=1), 1.0)
        before = _dividend(Market.HK, window_start - timedelta(days=1), 1.0)
        result = dividend_yield_factor(
            (on_edge, at_start, after, before),
            100.0,
            _DECISION_DATE,
            lookback_days=365,
        )
        self.assertEqual(result.dividend_count, 2)  # on-edge and window-start only
        self.assertEqual(result.eligible_sum, 2.0)

    def test_special_dividend_excluded_by_default(self) -> None:
        dividends = (
            _dividend(Market.HK, _DECISION_DATE - timedelta(days=30), 1.0),
            _dividend(Market.HK, _DECISION_DATE - timedelta(days=10), 5.0, is_special=True),
        )
        default = dividend_yield_factor(dividends, 100.0, _DECISION_DATE, lookback_days=365)
        self.assertEqual(default.eligible_sum, 1.0)
        self.assertEqual(default.special_sum, 5.0)
        included = dividend_yield_factor(
            dividends,
            100.0,
            _DECISION_DATE,
            lookback_days=365,
            include_special=True,
        )
        self.assertEqual(included.eligible_sum, 6.0)
        self.assertAlmostEqual(included.value, 0.06)

    def test_market_dividend_currencies_handled_consistently(self) -> None:
        hk = dividend_yield_factor(
            (_dividend(Market.HK, _DECISION_DATE - timedelta(days=30), 2.0),),
            100.0,
            _DECISION_DATE,
        )
        us = dividend_yield_factor(
            (_dividend(Market.US, _DECISION_DATE - timedelta(days=30), 2.0),),
            100.0,
            _DECISION_DATE,
        )
        # The factor is currency-agnostic at this stage: same math for HKD/USD.
        self.assertEqual(hk.value, us.value)
        self.assertEqual(hk.eligible_sum, 2.0)


class DividendSustainabilityFactorUnitTests(unittest.TestCase):
    """Dividend sustainability factor (SP 2.18) dimensions."""

    def test_normal_full_continuity_scores_one(self) -> None:
        dividends = tuple(
            _dividend(Market.HK, _DECISION_DATE - timedelta(days=30 * month), 1.0)
            for month in range(1, 5)
        )
        result = dividend_sustainability_factor(
            dividends,
            _fundamental(
                Market.HK, _DECISION_DATE - timedelta(days=60), _DECISION_DATE - timedelta(days=30)
            ),
            _DECISION_DATE,
            config=DividendSustainabilityConfig(lookback_days=365, expected_payments=4),
        )
        self.assertEqual(result.continuity_score, 1.0)
        self.assertAlmostEqual(result.value, 1.0)  # (continuity + payout) / 2

    def test_missing_fundamental_makes_score_unavailable(self) -> None:
        dividends = tuple(
            _dividend(Market.HK, _DECISION_DATE - timedelta(days=30 * month), 1.0)
            for month in range(1, 5)
        )
        result = dividend_sustainability_factor(dividends, None, _DECISION_DATE)
        self.assertIsNone(result.value)
        self.assertIn("no point-in-time financial data", result.missing_reason or "")

    def test_non_positive_net_income_makes_score_unavailable(self) -> None:
        fundamental = _fundamental(Market.HK, _DECISION_DATE - timedelta(days=60), _DECISION_DATE)
        fundamental = FundamentalRecord(
            fundamental.market,
            fundamental.symbol,
            fundamental.report_date,
            fundamental.fiscal_period,
            fundamental.available_on,
            roe=fundamental.roe,
            net_income=-1.0e9,
            total_equity=fundamental.total_equity,
            revenue=fundamental.revenue,
        )
        dividends = (_dividend(Market.HK, _DECISION_DATE - timedelta(days=30)),)
        result = dividend_sustainability_factor(dividends, fundamental, _DECISION_DATE)
        self.assertIsNone(result.value)
        self.assertIsNone(result.payout_ratio)

    def test_boundary_dates_payment_on_decision_date_counts(self) -> None:
        on_edge = _dividend(Market.HK, _DECISION_DATE, 1.0)
        after = _dividend(Market.HK, _DECISION_DATE + timedelta(days=1), 1.0)
        result = dividend_sustainability_factor(
            (on_edge, after),
            _fundamental(Market.HK, _DECISION_DATE - timedelta(days=60), _DECISION_DATE),
            _DECISION_DATE,
        )
        self.assertEqual(result.regular_payments, 1)

    def test_special_dividend_excluded_by_default(self) -> None:
        dividends = (
            _dividend(Market.HK, _DECISION_DATE - timedelta(days=30), 1.0),
            _dividend(Market.HK, _DECISION_DATE - timedelta(days=10), 3.0, is_special=True),
        )
        default = dividend_sustainability_factor(
            dividends,
            _fundamental(Market.HK, _DECISION_DATE - timedelta(days=60), _DECISION_DATE),
            _DECISION_DATE,
        )
        self.assertEqual(default.regular_sum, 1.0)
        self.assertEqual(default.special_sum, 3.0)
        included = dividend_sustainability_factor(
            dividends,
            _fundamental(Market.HK, _DECISION_DATE - timedelta(days=60), _DECISION_DATE),
            _DECISION_DATE,
            config=DividendSustainabilityConfig(include_special=True),
        )
        self.assertEqual(included.regular_sum, 4.0)
        self.assertEqual(included.regular_payments, 2)

    def test_market_dividend_currencies_handled_consistently(self) -> None:
        hk = dividend_sustainability_factor(
            (_dividend(Market.HK, _DECISION_DATE - timedelta(days=30), 1.0),),
            _fundamental(Market.HK, _DECISION_DATE - timedelta(days=60), _DECISION_DATE),
            _DECISION_DATE,
        )
        us = dividend_sustainability_factor(
            (_dividend(Market.US, _DECISION_DATE - timedelta(days=30), 1.0),),
            _fundamental(Market.US, _DECISION_DATE - timedelta(days=60), _DECISION_DATE),
            _DECISION_DATE,
        )
        self.assertEqual(hk.value, us.value)


class VolatilityFactorUnitTests(unittest.TestCase):
    """Annualized volatility factor (SP 2.19) dimensions."""

    def _window(self, min_observations: int = 2) -> WindowConfig:
        return WindowConfig(lookback_days=30, min_observations=min_observations)

    def test_normal_annualized_volatility(self) -> None:
        quotes = (
            _quote(Market.HK, _DECISION_DATE - timedelta(days=2), 100.0),
            _quote(Market.HK, _DECISION_DATE - timedelta(days=1), 110.0),
            _quote(Market.HK, _DECISION_DATE, 99.0),
        )
        result = annualized_volatility_factor(
            quotes,
            _DECISION_DATE,
            config=VolatilityConfig(window=self._window(), annual_trading_days=252),
        )
        expected = 0.1 * math.sqrt(252)  # returns [0.1, -0.1], population std 0.1
        self.assertAlmostEqual(result.value, expected)
        self.assertAlmostEqual(result.daily_volatility or 0.0, 0.1)

    def test_missing_observations_make_volatility_unavailable(self) -> None:
        single = (_quote(Market.HK, _DECISION_DATE, 100.0),)
        result = annualized_volatility_factor(
            single,
            _DECISION_DATE,
            config=VolatilityConfig(
                window=self._window(min_observations=2), annual_trading_days=252
            ),
        )
        self.assertIsNone(result.value)
        # Two quotes yield one return: not enough for a standard deviation.
        two = (
            _quote(Market.HK, _DECISION_DATE - timedelta(days=1), 100.0),
            _quote(Market.HK, _DECISION_DATE, 110.0),
        )
        result = annualized_volatility_factor(
            two,
            _DECISION_DATE,
            config=VolatilityConfig(
                window=self._window(min_observations=2), annual_trading_days=252
            ),
        )
        self.assertIsNone(result.value)

    def test_boundary_dates_future_quotes_excluded(self) -> None:
        base = (
            _quote(Market.HK, _DECISION_DATE - timedelta(days=2), 100.0),
            _quote(Market.HK, _DECISION_DATE - timedelta(days=1), 110.0),
            _quote(Market.HK, _DECISION_DATE, 99.0),
        )
        clean = annualized_volatility_factor(
            base,
            _DECISION_DATE,
            config=VolatilityConfig(window=self._window(), annual_trading_days=252),
        )
        contaminated = annualized_volatility_factor(
            base + (_quote(Market.HK, _DECISION_DATE + timedelta(days=1), 0.001),),
            _DECISION_DATE,
            config=VolatilityConfig(window=self._window(), annual_trading_days=252),
        )
        self.assertEqual(clean, contaminated)

    def test_market_difference_annualization(self) -> None:
        quotes = (
            _quote(Market.HK, _DECISION_DATE - timedelta(days=2), 100.0),
            _quote(Market.HK, _DECISION_DATE - timedelta(days=1), 110.0),
            _quote(Market.HK, _DECISION_DATE, 99.0),
        )
        hk = annualized_volatility_factor(
            quotes,
            _DECISION_DATE,
            config=VolatilityConfig(window=self._window(), annual_trading_days=242),
        )
        us = annualized_volatility_factor(
            quotes,
            _DECISION_DATE,
            config=VolatilityConfig(window=self._window(), annual_trading_days=252),
        )
        self.assertAlmostEqual(hk.value or 0.0, 0.1 * math.sqrt(242))
        self.assertAlmostEqual(us.value or 0.0, 0.1 * math.sqrt(252))
        self.assertNotAlmostEqual(hk.value or 0.0, us.value or 0.0)


class DrawdownLiquidityFactorUnitTests(unittest.TestCase):
    """Drawdown and liquidity factor (SP 2.20) dimensions."""

    _CALENDAR = _WeekdayCalendar()

    def _window(self, min_observations: int = 2) -> WindowConfig:
        return WindowConfig(lookback_days=10, min_observations=min_observations)

    def test_normal_drawdown_turnover_and_suspension(self) -> None:
        quotes = (
            _quote(Market.HK, date(2026, 3, 25), 100.0, 1_000),
            _quote(Market.HK, date(2026, 3, 26), 120.0, 1_000),
            _quote(Market.HK, date(2026, 3, 27), 90.0, 1_000),
            _quote(Market.HK, date(2026, 3, 30), 105.0, 1_000),
        )
        result = drawdown_liquidity_factor(
            Market.HK,
            quotes,
            _DECISION_DATE,
            self._CALENDAR,
            config=DrawdownLiquidityConfig(window=self._window()),
        )
        self.assertAlmostEqual(result.max_drawdown or 0.0, -0.25)
        self.assertAlmostEqual(result.average_turnover or 0.0, 103_750.0)
        expected_days = len(
            self._CALENDAR.trading_days(Market.HK, date(2026, 3, 21), _DECISION_DATE)
        )
        self.assertEqual(result.observed_days, 4)
        self.assertEqual(result.expected_days, expected_days)
        self.assertAlmostEqual(result.suspension_ratio or 0.0, max(0.0, 1.0 - 4 / expected_days))

    def test_missing_observations_make_metrics_unavailable(self) -> None:
        single = (_quote(Market.HK, _DECISION_DATE, 100.0, 1_000),)
        result = drawdown_liquidity_factor(
            Market.HK,
            single,
            _DECISION_DATE,
            self._CALENDAR,
            config=DrawdownLiquidityConfig(window=self._window(min_observations=2)),
        )
        self.assertIsNone(result.max_drawdown)
        self.assertIsNone(result.average_turnover)

    def test_suspension_unassessable_when_calendar_empty(self) -> None:
        quotes = (
            _quote(Market.HK, _DECISION_DATE - timedelta(days=1), 100.0, 1_000),
            _quote(Market.HK, _DECISION_DATE, 105.0, 1_000),
        )
        result = drawdown_liquidity_factor(
            Market.HK,
            quotes,
            _DECISION_DATE,
            _EmptyCalendar(),
            config=DrawdownLiquidityConfig(window=self._window()),
        )
        self.assertIsNone(result.suspension_ratio)
        self.assertEqual(result.expected_days, 0)

    def test_boundary_dates_future_quotes_excluded(self) -> None:
        base = (
            _quote(Market.HK, date(2026, 3, 25), 100.0, 1_000),
            _quote(Market.HK, date(2026, 3, 26), 120.0, 1_000),
            _quote(Market.HK, date(2026, 3, 27), 90.0, 1_000),
        )
        clean = drawdown_liquidity_factor(
            Market.HK,
            base,
            _DECISION_DATE,
            self._CALENDAR,
            config=DrawdownLiquidityConfig(window=self._window()),
        )
        contaminated = drawdown_liquidity_factor(
            Market.HK,
            base + (_quote(Market.HK, _DECISION_DATE + timedelta(days=1), 0.001, 1),),
            _DECISION_DATE,
            self._CALENDAR,
            config=DrawdownLiquidityConfig(window=self._window()),
        )
        self.assertEqual(clean, contaminated)

    def test_market_difference_per_market_calendar(self) -> None:
        # 2026-03-30 is a US-only holiday; HK trades it.
        calendar = _WeekdayCalendar(holidays=frozenset({date(2026, 3, 30)}))
        hk_quotes = (
            _quote(Market.HK, date(2026, 3, 25), 100.0, 1_000),
            _quote(Market.HK, date(2026, 3, 26), 100.0, 1_000),
            _quote(Market.HK, date(2026, 3, 27), 100.0, 1_000),
            _quote(Market.HK, date(2026, 3, 30), 100.0, 1_000),
        )
        us_quotes = (
            _quote(Market.US, date(2026, 3, 25), 100.0, 1_000),
            _quote(Market.US, date(2026, 3, 26), 100.0, 1_000),
            _quote(Market.US, date(2026, 3, 27), 100.0, 1_000),
        )
        hk = drawdown_liquidity_factor(
            Market.HK,
            hk_quotes,
            _DECISION_DATE,
            calendar,
            config=DrawdownLiquidityConfig(window=self._window()),
        )
        us = drawdown_liquidity_factor(
            Market.US,
            us_quotes,
            _DECISION_DATE,
            calendar,
            config=DrawdownLiquidityConfig(window=self._window()),
        )
        self.assertEqual(hk.observed_days, 4)
        self.assertEqual(us.observed_days, 3)
        self.assertNotAlmostEqual(hk.suspension_ratio or 0.0, us.suspension_ratio or 0.0)


class EarningsQualityFactorUnitTests(unittest.TestCase):
    """Earnings quality factor (SP 2.21) dimensions.

    The factor has no dividend or market parameter, so it covers normal,
    missing and disclosure-date boundaries.
    """

    def test_normal_component_scores_averaged(self) -> None:
        result = earnings_quality_factor(
            _fundamental(
                Market.HK, _DECISION_DATE - timedelta(days=60), _DECISION_DATE - timedelta(days=30)
            ),
            _DECISION_DATE,
        )
        self.assertAlmostEqual(result.roe_score or 0.0, 0.5)  # 0.15 / 0.3
        self.assertEqual(result.net_income_score, 1.0)
        self.assertEqual(result.revenue_score, 1.0)
        self.assertEqual(result.equity_score, 1.0)
        self.assertAlmostEqual(result.value or 0.0, 0.875)

    def test_missing_fundamental_makes_score_unavailable(self) -> None:
        result = earnings_quality_factor(None, _DECISION_DATE)
        self.assertIsNone(result.value)
        self.assertIn("no point-in-time financial data", result.missing_reason or "")

    def test_missing_fields_skipped_not_zeroed(self) -> None:
        fundamental = _fundamental(Market.HK, _DECISION_DATE - timedelta(days=60), _DECISION_DATE)
        fundamental = FundamentalRecord(
            fundamental.market,
            fundamental.symbol,
            fundamental.report_date,
            fundamental.fiscal_period,
            fundamental.available_on,
            roe=None,
            net_income=fundamental.net_income,
            total_equity=fundamental.total_equity,
            revenue=fundamental.revenue,
        )
        result = earnings_quality_factor(fundamental, _DECISION_DATE)
        self.assertIn("roe", result.missing_fields)
        self.assertAlmostEqual(result.value or 0.0, 1.0)  # mean of the three present
        self.assertIsNone(result.roe_score)

    def test_all_fields_missing_makes_score_unavailable(self) -> None:
        fundamental = FundamentalRecord(
            Market.HK,
            "SYM",
            _DECISION_DATE - timedelta(days=60),
            "2025",
            _DECISION_DATE,
        )
        result = earnings_quality_factor(fundamental, _DECISION_DATE)
        self.assertIsNone(result.value)
        self.assertIn("no ROE", result.missing_reason or "")

    def test_boundary_disclosure_on_decision_date_is_usable(self) -> None:
        result = earnings_quality_factor(
            _fundamental(Market.HK, _DECISION_DATE - timedelta(days=60), _DECISION_DATE),
            _DECISION_DATE,
        )
        self.assertIsNotNone(result.value)
        self.assertEqual(result.available_on, _DECISION_DATE)

    def test_boundary_future_disclosure_refused(self) -> None:
        result = earnings_quality_factor(
            _fundamental(
                Market.HK, _DECISION_DATE + timedelta(days=1), _DECISION_DATE + timedelta(days=1)
            ),
            _DECISION_DATE,
        )
        self.assertIsNone(result.value)
        self.assertIn("not yet available", result.missing_reason or "")

    def test_missing_disclosure_date_refused(self) -> None:
        result = earnings_quality_factor(
            _fundamental(Market.HK, _DECISION_DATE - timedelta(days=60), None),
            _DECISION_DATE,
        )
        self.assertIsNone(result.value)
        self.assertIn("no known disclosure date", result.missing_reason or "")


if __name__ == "__main__":
    unittest.main()
