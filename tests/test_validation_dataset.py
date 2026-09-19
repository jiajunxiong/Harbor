"""Coverage measurements and gap wording for the frozen dataset (SP 5.30).

These run without a database: they pin the two mistakes that real data exposed in
this code, both of which a passing dashboard would have hidden inside a number.

1. An item with *nothing* missing rendered a bare prefix (``股票池中无行情数据的证券``)
   as its gap, which reads as "this is a gap" — a fully covered stock pool carried
   a warning about its own missing quotes. An empty offender list must produce an
   empty gap.
2. Every ratio is a coverage claim, so its denominator has to be a real count and
   never zero: a market with no trading days or no pool must score as a gap rather
   than as a perfect ``0/0``.
"""

from __future__ import annotations

import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.validation_domain import ManifestComponent
from harbor.services.validation_dataset import (
    BENCHMARK_GAP,
    CALENDAR_CAVEAT,
    _gap,
    _MarketFacts,
    _measurements,
)


def _facts(**overrides: object) -> _MarketFacts:
    """A fully covered single-market day, with the given fields replaced."""
    base: dict[str, object] = {
        "market": Market.HK,
        "pool": ("0001.HK", "0002.HK"),
        "trading_days": (),
        "quote_days": 3,
        "symbols_without_quotes": (),
        "symbols_without_fundamentals": (),
        "actions": 0,
        "actions_with_terms": 0,
        "fx_pair": None,
        "fx_days": 0,
    }
    base.update(overrides)
    return _MarketFacts(**base)  # type: ignore[arg-type]


def _facts_with_days(**overrides: object) -> _MarketFacts:
    """The same day, but with three trading days so ratios have a denominator."""
    days = (date(2022, 1, 3), date(2022, 1, 4), date(2022, 1, 5))
    defaults: dict[str, object] = {"trading_days": days, "quote_days": 3}
    defaults.update(overrides)
    return _facts(**defaults)


class GapMessageTests(unittest.TestCase):
    def test_nothing_missing_produces_no_gap(self) -> None:
        # The regression: a bare prefix used to count as an explicit gap.
        self.assertEqual(_gap("股票池中无行情数据的证券", ()), "")

    def test_a_few_offenders_are_named(self) -> None:
        gap = _gap("股票池中无行情数据的证券", ("0001.HK", "0002.HK"))

        self.assertIn("0001.HK, 0002.HK", gap)
        self.assertNotIn("等", gap)

    def test_a_long_list_is_truncated_and_counted(self) -> None:
        names = tuple(f"000{index}.HK" for index in range(1, 9))

        gap = _gap("股票池中无财报数据的证券", names)

        self.assertIn("0001.HK, 0002.HK, 0003.HK", gap)
        self.assertIn("等 8 个", gap)
        self.assertNotIn("0004.HK", gap)


class MeasurementTests(unittest.TestCase):
    def test_every_ratio_is_well_formed(self) -> None:
        measurements = _measurements(_facts_with_days())

        self.assertEqual(len(measurements), 7)
        for item, measurement in measurements.items():
            self.assertGreaterEqual(measurement.denominator, 1, msg=item.value)
            self.assertGreaterEqual(measurement.covered, 0, msg=item.value)
            self.assertLessEqual(measurement.covered, measurement.denominator, msg=item.value)

    def test_a_covered_pool_does_not_report_its_own_gap(self) -> None:
        measurements = _measurements(_facts_with_days())

        # Every pool member has quotes and financials in this day.
        self.assertEqual(measurements[ManifestComponent.STOCK_POOL].gap, "")
        self.assertEqual(measurements[ManifestComponent.STOCK_POOL].coverage_pct, 100.0)
        self.assertEqual(measurements[ManifestComponent.FUNDAMENTALS].gap, "")

    def test_a_missing_pool_member_is_named(self) -> None:
        measurements = _measurements(
            _facts_with_days(symbols_without_quotes=("0002.HK",)),
        )

        stock_pool = measurements[ManifestComponent.STOCK_POOL]
        self.assertEqual(stock_pool.covered, 1)
        self.assertEqual(stock_pool.denominator, 2)
        self.assertIn("0002.HK", stock_pool.gap)

    def test_an_empty_pool_scores_zero_rather_than_dividing_by_nothing(self) -> None:
        measurements = _measurements(_facts_with_days(pool=(), quote_days=0))

        for item in (ManifestComponent.STOCK_POOL, ManifestComponent.FUNDAMENTALS):
            self.assertEqual(measurements[item].covered, 0, msg=item.value)
            self.assertEqual(measurements[item].denominator, 1, msg=item.value)
            self.assertTrue(measurements[item].gap, msg=item.value)

    def test_missing_quotes_name_the_shortfall_in_trading_days(self) -> None:
        measurements = _measurements(_facts_with_days(quote_days=1))

        prices = measurements[ManifestComponent.PRICES]
        self.assertEqual((prices.covered, prices.denominator), (1, 3))
        self.assertIn("2 个交易日缺少行情数据", prices.gap)

    def test_actions_without_terms_are_reported_as_unusable(self) -> None:
        measurements = _measurements(_facts_with_days(actions=4, actions_with_terms=3))

        actions = measurements[ManifestComponent.CORPORATE_ACTIONS]
        self.assertEqual((actions.covered, actions.denominator), (3, 4))
        self.assertIn("1 条企业行动缺少条款记录", actions.gap)

    def test_actions_are_a_gap_only_when_the_window_truly_has_none(self) -> None:
        measurements = _measurements(_facts_with_days(actions=0, actions_with_terms=0))

        actions = measurements[ManifestComponent.CORPORATE_ACTIONS]
        self.assertEqual((actions.covered, actions.denominator), (0, 1))
        self.assertIn("没有企业行动记录", actions.gap)

    def test_a_base_currency_market_needs_no_fx(self) -> None:
        measurements = _measurements(_facts_with_days(fx_pair=None))

        fx = measurements[ManifestComponent.FX]
        self.assertEqual((fx.covered, fx.denominator), (1, 1))
        self.assertEqual(fx.gap, "")

    def test_a_short_fx_series_names_the_pair_it_needs(self) -> None:
        measurements = _measurements(
            _facts_with_days(fx_pair=("HKD", "CNY"), fx_days=2),
        )

        fx = measurements[ManifestComponent.FX]
        self.assertEqual((fx.covered, fx.denominator), (2, 3))
        self.assertIn("HKD->CNY", fx.gap)

    def test_the_calendar_caveat_is_recorded_on_every_run(self) -> None:
        measurements = _measurements(_facts_with_days())

        # It scores 1/1 (the calendar ships with the code) yet keeps a gap text so
        # the illustrative-holiday caveat (SP 2.74) cannot be scrolled past.
        calendar = measurements[ManifestComponent.CALENDAR]
        self.assertEqual((calendar.covered, calendar.denominator), (1, 1))
        self.assertEqual(calendar.gap, CALENDAR_CAVEAT)

    def test_the_benchmark_is_reported_as_unavailable(self) -> None:
        measurements = _measurements(_facts_with_days())

        benchmark = measurements[ManifestComponent.BENCHMARK]
        self.assertEqual((benchmark.covered, benchmark.denominator), (0, 1))
        self.assertEqual(benchmark.gap, BENCHMARK_GAP)
        self.assertEqual(benchmark.coverage_pct, 0.0)


if __name__ == "__main__":
    unittest.main()
