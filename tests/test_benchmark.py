"""Benchmark definition and resolution tests (MVP 2 / SP 2.52).

Verifies cash, single-market-index and configurable blended benchmarks; that a
resolved series covers every trading day; that missing reliable benchmark data
refuses the series (禁止虚构超额收益) rather than fabricating a value; and that
excess return is only computable over a resolved benchmark.
"""

import unittest
from datetime import date

from harbor.core.backtest_config import (
    BenchmarkComponent,
    BenchmarkConfig,
    BenchmarkKind,
)
from harbor.core.backtest_domain import Market
from harbor.core.benchmark import (
    BenchmarkDataError,
    BenchmarkLevel,
    BenchmarkSeries,
    excess_return,
    resolve_benchmark_series,
)

HK = Market.HK
US = Market.US

_DAYS = (date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5))


def _levels(market: Market, symbol: str) -> dict[date, float]:
    """Return a fixed, rising index level series for a symbol."""
    base = {"HSTECH": 100.0, "SPX": 500.0}
    start = base[symbol]
    return {day: start * (1.0 + 0.01 * index) for index, day in enumerate(_DAYS)}


def _index_level(market: Market, symbol: str, day: date) -> float | None:
    return _levels(market, symbol).get(day)


class ConfigValidationTests(unittest.TestCase):
    """Verify the benchmark configuration validation (SP 2.4/2.52)."""

    def test_cash_benchmark_default(self) -> None:
        config = BenchmarkConfig()
        self.assertEqual(config.kind, BenchmarkKind.CASH)
        self.assertEqual(config.cash_weight, 0.0)

    def test_market_index_requires_market_and_symbol(self) -> None:
        config = BenchmarkConfig(kind=BenchmarkKind.MARKET_INDEX, market=HK, symbol="HSTECH")
        self.assertEqual(config.symbol, "HSTECH")
        with self.assertRaises(ValueError):
            BenchmarkConfig(kind=BenchmarkKind.MARKET_INDEX, market=HK, symbol="")
        with self.assertRaises(ValueError):
            BenchmarkConfig(kind=BenchmarkKind.MARKET_INDEX, symbol="HSTECH")

    def test_market_index_cannot_carry_components(self) -> None:
        with self.assertRaises(ValueError):
            BenchmarkConfig(
                kind=BenchmarkKind.MARKET_INDEX,
                market=HK,
                symbol="HSTECH",
                components=(BenchmarkComponent(market=US, symbol="SPX", weight=0.5),),
            )

    def test_cash_cannot_carry_index_data(self) -> None:
        with self.assertRaises(ValueError):
            BenchmarkConfig(kind=BenchmarkKind.CASH, market=HK, symbol="HSTECH")

    def test_blended_requires_components(self) -> None:
        with self.assertRaises(ValueError):
            BenchmarkConfig(kind=BenchmarkKind.BLENDED, components=())

    def test_blended_weights_must_sum_with_cash(self) -> None:
        config = BenchmarkConfig(
            kind=BenchmarkKind.BLENDED,
            cash_weight=0.5,
            components=(BenchmarkComponent(market=HK, symbol="HSTECH", weight=0.5),),
        )
        self.assertEqual(len(config.components), 1)
        with self.assertRaises(ValueError):
            BenchmarkConfig(
                kind=BenchmarkKind.BLENDED,
                cash_weight=0.5,
                components=(BenchmarkComponent(market=HK, symbol="HSTECH", weight=0.4),),
            )


class CashBenchmarkTests(unittest.TestCase):
    """Verify the cash benchmark is flat with a zero return (SP 2.52)."""

    def test_cash_series_is_flat(self) -> None:
        series = resolve_benchmark_series(
            config=BenchmarkConfig(),
            days=_DAYS,
            index_level=_index_level,
        )
        self.assertIsInstance(series, BenchmarkSeries)
        self.assertEqual(series.kind, BenchmarkKind.CASH)
        self.assertEqual(len(series.levels), len(_DAYS))
        self.assertEqual([level.level for level in series.levels], [1.0] * len(_DAYS))

    def test_cash_total_return_is_zero(self) -> None:
        series = resolve_benchmark_series(
            config=BenchmarkConfig(), days=_DAYS, index_level=_index_level
        )
        self.assertEqual(series.total_return(), 0.0)
        self.assertEqual(series.returns(), (0.0, 0.0, 0.0))


class MarketIndexBenchmarkTests(unittest.TestCase):
    """Verify a single market-index benchmark (SP 2.52)."""

    def test_index_series_uses_provider_levels(self) -> None:
        config = BenchmarkConfig(kind=BenchmarkKind.MARKET_INDEX, market=HK, symbol="HSTECH")
        series = resolve_benchmark_series(config=config, days=_DAYS, index_level=_index_level)
        self.assertEqual(len(series.levels), len(_DAYS))
        self.assertEqual(series.levels[0].level, 100.0)
        self.assertEqual(series.levels[-1].level, 103.0)
        self.assertAlmostEqual(series.total_return(), 0.03, places=6)

    def test_index_missing_level_is_refused(self) -> None:
        config = BenchmarkConfig(kind=BenchmarkKind.MARKET_INDEX, market=US, symbol="SPX")

        def missing(market: Market, symbol: str, day: date) -> float | None:
            return None if day == _DAYS[2] else _index_level(market, symbol, day)

        with self.assertRaisesRegex(BenchmarkDataError, "refusing to fabricate"):
            resolve_benchmark_series(config=config, days=_DAYS, index_level=missing)

    def test_index_levels_must_be_positive(self) -> None:
        config = BenchmarkConfig(kind=BenchmarkKind.MARKET_INDEX, market=HK, symbol="HSTECH")

        def zero(market: Market, symbol: str, day: date) -> float | None:
            return 0.0

        with self.assertRaises(BenchmarkDataError):
            resolve_benchmark_series(config=config, days=_DAYS, index_level=zero)


class BlendedBenchmarkTests(unittest.TestCase):
    """Verify the configurable blended benchmark (SP 2.52)."""

    def test_blended_mixes_cash_and_index(self) -> None:
        config = BenchmarkConfig(
            kind=BenchmarkKind.BLENDED,
            cash_weight=0.25,
            components=(BenchmarkComponent(market=HK, symbol="HSTECH", weight=0.75),),
        )
        series = resolve_benchmark_series(config=config, days=_DAYS, index_level=_index_level)
        self.assertEqual(series.kind, BenchmarkKind.BLENDED)
        # level = 0.25*1.0 + 0.75*index_level
        self.assertAlmostEqual(series.levels[0].level, 0.25 + 0.75 * 100.0, places=6)
        self.assertAlmostEqual(series.levels[-1].level, 0.25 + 0.75 * 103.0, places=6)

    def test_blended_two_index_legs(self) -> None:
        config = BenchmarkConfig(
            kind=BenchmarkKind.BLENDED,
            components=(
                BenchmarkComponent(market=HK, symbol="HSTECH", weight=0.5),
                BenchmarkComponent(market=US, symbol="SPX", weight=0.5),
            ),
        )
        series = resolve_benchmark_series(config=config, days=_DAYS, index_level=_index_level)
        self.assertAlmostEqual(series.levels[0].level, 0.5 * 100.0 + 0.5 * 500.0, places=6)

    def test_blended_missing_leg_is_refused(self) -> None:
        config = BenchmarkConfig(
            kind=BenchmarkKind.BLENDED,
            components=(
                BenchmarkComponent(market=HK, symbol="HSTECH", weight=0.5),
                BenchmarkComponent(market=US, symbol="SPX", weight=0.5),
            ),
        )

        def missing_spx(market: Market, symbol: str, day: date) -> float | None:
            if symbol == "SPX":
                return None
            return _index_level(market, symbol, day)

        with self.assertRaises(BenchmarkDataError):
            resolve_benchmark_series(config=config, days=_DAYS, index_level=missing_spx)


class SeriesBehaviourTests(unittest.TestCase):
    """Verify series return helpers and excess return (SP 2.52)."""

    def test_single_day_series_has_zero_return(self) -> None:
        series = BenchmarkSeries(
            kind=BenchmarkKind.CASH,
            levels=(BenchmarkLevel(as_of=_DAYS[0], level=1.0, kind=BenchmarkKind.CASH),),
        )
        self.assertEqual(series.total_return(), 0.0)
        self.assertEqual(series.returns(), ())

    def test_empty_series_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one level"):
            BenchmarkSeries(kind=BenchmarkKind.CASH, levels=())

    def test_nonpositive_level_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            BenchmarkSeries(
                kind=BenchmarkKind.CASH,
                levels=(
                    BenchmarkLevel(as_of=_DAYS[0], level=1.0, kind=BenchmarkKind.CASH),
                    BenchmarkLevel(as_of=_DAYS[1], level=0.0, kind=BenchmarkKind.CASH),
                ),
            )

    def test_excess_return_subtracts_benchmark(self) -> None:
        self.assertAlmostEqual(excess_return(portfolio_return=0.05, benchmark_return=0.03), 0.02)

    def test_readable(self) -> None:
        series = resolve_benchmark_series(
            config=BenchmarkConfig(), days=_DAYS, index_level=_index_level
        )
        self.assertIn("total return 0.0000%", series.readable())


if __name__ == "__main__":
    unittest.main()
