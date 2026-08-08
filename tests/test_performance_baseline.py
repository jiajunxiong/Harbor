"""Performance baseline tests (MVP 2 / SP 2.84).

Records the runtime and memory baseline at the target scale — a single market
(US), 20 symbols, 5 years of daily data, quarterly rebalancing — and guards
against gross regressions with generous, machine-tolerant ceilings (性能基线).
The benchmark runs the SP 2.51 end-to-end runner once per test class; the
measured time and peak traced memory are compared against the documented
baseline (``docs/performance_baseline.md``).

The ceilings are deliberately loose (30 s / 512 MiB, many multiples of the
measured ~2 s / ~6 MiB) so the test never flakes on a slow or loaded machine
while still catching a catastrophic performance regression. The suite is
self-contained and deterministic (fixed Mock data, no randomness).
"""

import time
import tracemalloc
import unittest
from collections.abc import Sequence
from datetime import date, timedelta

from harbor.core.backtest_config import (
    BacktestConfig,
    MarketQuota,
    RebalanceFrequency,
    RiskConfig,
)
from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import DailyQuote, TradingCalendar
from harbor.core.backtest_runner import BacktestTrace, MockUniverse, run_end_to_end_backtest
from harbor.core.target_weight import TargetWeightConfig, WeightingMethod

US = Market.US
USD = Currency.USD

_START = date(2019, 1, 1)
_END = date(2023, 12, 31)
_SYMBOL_COUNT = 20

# Documented baseline ceilings (see docs/performance_baseline.md).
_RUNTIME_CEILING_SECONDS = 30.0
_MEMORY_CEILING_BYTES = 512 * 1024 * 1024  # 512 MiB


def _weekdays(start: date, end: date) -> list[date]:
    """Return weekdays (Mon-Fri) in the inclusive range."""
    days: list[date] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _quarter_starts(start: date, end: date) -> list[date]:
    """Return the first weekday of each quarter within [start, end]."""
    days: list[date] = []
    year, month = start.year, start.month
    while True:
        quarter_month = ((month - 1) // 3) * 3 + 1
        day = date(year, quarter_month, 1)
        if day > end:
            break
        while day.weekday() >= 5:
            day += timedelta(days=1)
        if day >= start:
            days.append(day)
        month += 3
        if month > 12:
            month, year = 1, year + 1
    return days


class _WeekdayCalendar(TradingCalendar):
    """A deterministic Mon-Fri trading calendar (target-scale benchmark)."""

    def is_trading_day(self, market: Market, day: date) -> bool:
        return day.weekday() < 5

    def next_trading_day(self, market: Market, day: date) -> date:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return day

    def previous_trading_day(self, market: Market, day: date) -> date:
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return day

    def trading_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        return tuple(_weekdays(start, end))

    def rebalance_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        return ()


def _build_target_universe() -> MockUniverse:
    """Build the target-scale universe: 20 symbols, 5 years of daily data."""
    days = _weekdays(_START, _END)
    calendar = _WeekdayCalendar()
    symbols = tuple(f"SYM{i:02d}" for i in range(1, _SYMBOL_COUNT + 1))
    quotes: dict[tuple[Market, str], dict[date, DailyQuote]] = {}
    for index, symbol in enumerate(symbols):
        base = 50.0 + index
        per_symbol: dict[date, DailyQuote] = {}
        for offset, day in enumerate(days):
            price = base * (1.0 + (offset % 7) * 0.001)
            per_symbol[day] = DailyQuote(
                market=US,
                symbol=symbol,
                day=day,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=1_000_000,
                adjusted_close=price,
            )
        quotes[(US, symbol)] = per_symbol
    rebalance_days = _quarter_starts(_START, _END)
    selections = {(US, day): symbols for day in rebalance_days}
    return MockUniverse(calendar=calendar, quotes=quotes, selections=selections)


def _target_config() -> BacktestConfig:
    return BacktestConfig(
        markets=(US,),
        market_quotas=(MarketQuota(market=US, target_count=_SYMBOL_COUNT, weight=1.0),),
        start_date=_START,
        end_date=_END,
        base_currency=USD,
        rebalance_frequency=RebalanceFrequency.QUARTERLY,
        initial_capital=1_000_000.0,
        risk=RiskConfig(max_position_pct=1.0, max_market_pct=1.0, min_cash_pct=0.0),
    )


class PerformanceBaselineTests(unittest.TestCase):
    """Record and guard the target-scale runtime and memory baseline."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.universe = _build_target_universe()
        cls.config = _target_config()
        tracemalloc.start()
        start = time.perf_counter()
        cls.trace = run_end_to_end_backtest(
            run_id="bench",
            config=cls.config,
            universe=cls.universe,
            code_version="1.0.0",
            weighting=TargetWeightConfig(
                method=WeightingMethod.EQUAL,
                cash_weight=0.05,
                decimal_places=4,
            ),
        )
        cls.elapsed = time.perf_counter() - start
        _, cls.peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    def test_scale_is_target(self) -> None:
        """The benchmark runs at the documented target scale."""
        symbols = {symbol for (market, symbol) in self.universe.quotes if market is US}
        self.assertEqual(len(symbols), _SYMBOL_COUNT)
        self.assertEqual(len(self.universe.selections), 20)  # 5 years of quarters
        trading_days = len(self.universe.calendar.trading_days(US, _START, _END))
        self.assertGreaterEqual(trading_days, 1_200)  # ~5 x 252 US trading days
        quote_count = sum(len(quotes) for quotes in self.universe.quotes.values())
        self.assertEqual(quote_count, _SYMBOL_COUNT * trading_days)

    def test_target_scale_run_completes_and_reconciles(self) -> None:
        """The full target-scale run completes and reconciles every day."""
        trace = self.trace
        self.assertIsInstance(trace, BacktestTrace)
        self.assertTrue(trace.succeeded)
        self.assertEqual(trace.reconcile_all(), ())
        self.assertGreaterEqual(len(trace.results), 1_200)

    def test_runtime_baseline(self) -> None:
        """Runtime stays under the recorded baseline ceiling (SP 2.84)."""
        self.assertLess(self.elapsed, _RUNTIME_CEILING_SECONDS)

    def test_memory_baseline(self) -> None:
        """Peak traced memory stays under the recorded baseline ceiling."""
        self.assertLess(self.peak, _MEMORY_CEILING_BYTES)


if __name__ == "__main__":
    unittest.main()
