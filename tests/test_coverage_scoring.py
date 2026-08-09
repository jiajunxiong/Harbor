"""Data-quality coverage scoring tests (MVP 3 / SP 3.9).

Verifies per-market coverage quantification and gap identification for the
data a frozen dataset relies on: prices, the historical stock pool,
fundamentals availability, corporate-action terms, the trading calendar, FX
and the benchmark — including the manifest-derived window coverage (SP 3.6)
that turns an un-frozen component into a full gap.
"""

import unittest
from dataclasses import FrozenInstanceError
from datetime import date

from harbor.core.backtest_domain import Currency, Market
from harbor.core.coverage_scoring import (
    SCORED_ITEMS,
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
    coverage_from_manifest,
    score_market_coverage,
)
from harbor.core.validation_domain import (
    DataComponentManifest,
    DatasetManifest,
    ManifestComponent,
)


def _component(
    kind: ManifestComponent,
    *,
    start: date | None = date(2019, 1, 1),
    end: date | None = date(2024, 12, 31),
) -> DataComponentManifest:
    """Return a component record within the default manifest range."""
    return DataComponentManifest(
        component=kind,
        source="mock",
        version="2024-12",
        start=start,
        end=end,
    )


def _manifest(**overrides: object) -> DatasetManifest:
    """Return a valid manifest with every scored component fully covered."""
    fields: dict[str, object] = {
        "markets": (Market.HK, Market.US),
        "base_currency": Currency.HKD,
        "start_date": date(2019, 1, 1),
        "end_date": date(2024, 12, 31),
        "data_cutoff": date(2024, 12, 31),
        "config_hash": "abc123",
        "code_version": "1.0.0",
        "calendar_version": "hkex-2024",
        "fx_source": "mock",
        "fingerprint": "fp-1",
        "components": tuple(_component(kind) for kind in SCORED_ITEMS),
    }
    fields.update(overrides)
    return DatasetManifest(**fields)  # type: ignore[arg-type]


def _measurement(
    covered: int = 100,
    denominator: int = 100,
    gap: str = "",
) -> CoverageMeasurement:
    return CoverageMeasurement(covered=covered, denominator=denominator, gap=gap)


class ScoredItemsTests(unittest.TestCase):
    """Verify the scored coverage items match the SP 3.9 list."""

    def test_scored_items(self) -> None:
        self.assertEqual(
            [item.value for item in SCORED_ITEMS],
            [
                "prices",
                "stock_pool",
                "fundamentals",
                "corporate_actions",
                "calendar",
                "fx",
                "benchmark",
            ],
        )


class CoverageMeasurementTests(unittest.TestCase):
    """Verify a single coverage measurement."""

    def test_valid_measurement_and_percentage(self) -> None:
        measurement = _measurement(covered=80, denominator=100)
        self.assertEqual(measurement.coverage_pct, 80.0)

    def test_full_coverage_is_100_percent(self) -> None:
        self.assertEqual(_measurement().coverage_pct, 100.0)

    def test_rejects_zero_denominator(self) -> None:
        with self.assertRaisesRegex(ValueError, "denominator must be at least 1"):
            CoverageMeasurement(covered=0, denominator=0)

    def test_rejects_negative_covered(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be non-negative"):
            CoverageMeasurement(covered=-1, denominator=10)

    def test_rejects_covered_above_denominator(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceeds denominator"):
            CoverageMeasurement(covered=11, denominator=10)

    def test_is_frozen(self) -> None:
        measurement = _measurement()
        with self.assertRaises(FrozenInstanceError):
            measurement.covered = 50  # type: ignore[misc]


class CoverageScoreTests(unittest.TestCase):
    """Verify a per-item score."""

    def test_score_exposes_percentage(self) -> None:
        score = CoverageScore(
            market=Market.HK,
            item=ManifestComponent.PRICES,
            measurement=_measurement(covered=90, denominator=100),
        )
        self.assertEqual(score.coverage_pct, 90.0)
        self.assertTrue(score.is_gap)

    def test_full_coverage_is_not_a_gap(self) -> None:
        score = CoverageScore(
            market=Market.HK,
            item=ManifestComponent.PRICES,
            measurement=_measurement(),
        )
        self.assertFalse(score.is_gap)

    def test_explicit_gap_marks_the_score(self) -> None:
        score = CoverageScore(
            market=Market.HK,
            item=ManifestComponent.FX,
            measurement=_measurement(gap="USD rate missing"),
        )
        self.assertTrue(score.is_gap)

    def test_readable_includes_gap(self) -> None:
        score = CoverageScore(
            market=Market.HK,
            item=ManifestComponent.FX,
            measurement=_measurement(covered=50, denominator=100, gap="USD rate missing"),
        )
        readable = score.readable()
        self.assertIn("HK/fx", readable)
        self.assertIn("50.0%", readable)
        self.assertIn("USD rate missing", readable)


class MarketCoverageTests(unittest.TestCase):
    """Verify the per-market aggregate."""

    def _coverage(self) -> MarketCoverage:
        return score_market_coverage(
            Market.HK,
            {
                ManifestComponent.PRICES: _measurement(),
                ManifestComponent.FX: _measurement(covered=50, denominator=100),
                ManifestComponent.BENCHMARK: _measurement(),
            },
            scored=(
                ManifestComponent.PRICES,
                ManifestComponent.FX,
                ManifestComponent.BENCHMARK,
            ),
        )

    def test_overall_is_the_mean(self) -> None:
        coverage = self._coverage()
        self.assertAlmostEqual(coverage.overall_pct, (100.0 + 50.0 + 100.0) / 3)

    def test_score_lookup(self) -> None:
        coverage = self._coverage()
        self.assertIsNotNone(coverage.score(ManifestComponent.FX))
        self.assertIsNone(coverage.score(ManifestComponent.CALENDAR))

    def test_gaps_only_include_incomplete_items(self) -> None:
        coverage = self._coverage()
        gaps = coverage.gaps()
        self.assertEqual(len(gaps), 1)
        self.assertIs(gaps[0].item, ManifestComponent.FX)

    def test_readable_single_line(self) -> None:
        coverage = self._coverage()
        self.assertIn("HK overall", coverage.readable())
        self.assertIn("gaps 1", coverage.readable())

    def test_requires_at_least_one_score(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one score"):
            MarketCoverage(market=Market.HK, scores=())


class ScoreMarketCoverageTests(unittest.TestCase):
    """Verify measurement-based scoring (SP 3.9)."""

    def test_missing_measurement_is_a_full_gap(self) -> None:
        coverage = score_market_coverage(Market.HK, {})
        self.assertEqual(coverage.overall_pct, 0.0)
        self.assertEqual(len(coverage.gaps()), len(SCORED_ITEMS))
        prices = coverage.score(ManifestComponent.PRICES)
        self.assertIsNotNone(prices)
        self.assertIn("no coverage measurement for prices", prices.measurement.gap)

    def test_partial_measurements_are_quantified(self) -> None:
        coverage = score_market_coverage(
            Market.US,
            {ManifestComponent.PRICES: _measurement(covered=90, denominator=100)},
            scored=(ManifestComponent.PRICES,),
        )
        self.assertEqual(coverage.overall_pct, 90.0)
        self.assertEqual(len(coverage.gaps()), 1)

    def test_full_measurements_have_no_gaps(self) -> None:
        coverage = score_market_coverage(
            Market.US,
            {ManifestComponent.PRICES: _measurement()},
            scored=(ManifestComponent.PRICES,),
        )
        self.assertEqual(coverage.overall_pct, 100.0)
        self.assertEqual(coverage.gaps(), ())

    def test_explicit_gap_is_preserved(self) -> None:
        coverage = score_market_coverage(
            Market.HK,
            {ManifestComponent.CORPORATE_ACTIONS: _measurement(gap="terms missing")},
            scored=(ManifestComponent.CORPORATE_ACTIONS,),
        )
        score = coverage.score(ManifestComponent.CORPORATE_ACTIONS)
        self.assertIsNotNone(score)
        self.assertIn("terms missing", score.measurement.gap)


class CoverageFromManifestTests(unittest.TestCase):
    """Verify manifest-derived window coverage (SP 3.6/3.9)."""

    def test_full_window_is_fully_covered(self) -> None:
        coverage = coverage_from_manifest(_manifest(), Market.HK)
        self.assertEqual(coverage.overall_pct, 100.0)
        self.assertEqual(coverage.gaps(), ())

    def test_unfrozen_component_is_a_full_gap(self) -> None:
        components = tuple(
            _component(kind) for kind in SCORED_ITEMS if kind is not ManifestComponent.BENCHMARK
        )
        coverage = coverage_from_manifest(_manifest(components=components), Market.HK)
        benchmark = coverage.score(ManifestComponent.BENCHMARK)
        self.assertIsNotNone(benchmark)
        self.assertEqual(benchmark.coverage_pct, 0.0)
        self.assertIn("benchmark is not frozen in the manifest", benchmark.measurement.gap)

    def test_partial_component_range_is_quantified(self) -> None:
        components = tuple(
            _component(kind, start=date(2020, 1, 1), end=date(2024, 12, 31))
            if kind is ManifestComponent.FX
            else _component(kind)
            for kind in SCORED_ITEMS
        )
        coverage = coverage_from_manifest(_manifest(components=components), Market.HK)
        fx = coverage.score(ManifestComponent.FX)
        self.assertIsNotNone(fx)
        # 2020-01-01..2024-12-31 is 1827 of the 2192-day window.
        self.assertAlmostEqual(fx.coverage_pct, 1827 / 2192 * 100.0, places=4)
        self.assertEqual(len(coverage.gaps()), 1)

    def test_unbounded_component_is_a_gap(self) -> None:
        components = tuple(
            _component(kind, start=None, end=None)
            if kind is ManifestComponent.CALENDAR
            else _component(kind)
            for kind in SCORED_ITEMS
        )
        coverage = coverage_from_manifest(_manifest(components=components), Market.HK)
        calendar = coverage.score(ManifestComponent.CALENDAR)
        self.assertIsNotNone(calendar)
        self.assertEqual(calendar.coverage_pct, 0.0)
        self.assertIn("calendar query range is unbounded", calendar.measurement.gap)


if __name__ == "__main__":
    unittest.main()
