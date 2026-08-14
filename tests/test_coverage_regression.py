"""Coverage-gate regression tests (MVP 3 / SP 3.62, TEST-ONLY).

Confirms that the SP 3.10 coverage gate blocks or downgrades each of the seven
key data gaps accurately — prices (价格), the historical stock pool (股票池),
financial-statement availability (财报可得性), corporate-action terms (企业行动),
the trading calendar (日历), FX (FX) and the benchmark (基准) — and that the
three SP 3.10 acceptance gaps are never silently passed (缺失 FX、未知股票池或
关键企业行动缺口不得静默通过):

- a price / stock-pool percentage breach hard-blocks the run (``ERROR``);
- a required-but-missing FX / stock pool / corporate-action terms disqualifies
  the conclusion (``NOT_QUALIFIED``), and only degrades to a ``WARNING`` when
  the corresponding requirement flag is turned off;
- a fundamental shortfall, calendar gap or benchmark gap only warns (``WARNING``)
  and never blocks or disqualifies.

Gate-level aggregation is also covered: an ``ERROR`` blocks, a ``NOT_QUALIFIED``
disqualifies without hard-blocking, and warnings alone still pass.
"""

import unittest

from harbor.core.backtest_domain import Market
from harbor.core.coverage_gate import (
    CoverageGateResult,
    CoverageThresholdResult,
    evaluate_coverage,
)
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.validation_config import CoverageSeverity, CoverageThresholdConfig
from harbor.core.validation_domain import ManifestComponent


def _score(
    item: ManifestComponent,
    *,
    covered: int,
    denominator: int,
    gap: str = "",
) -> CoverageScore:
    """One per-market coverage score (SP 3.9)."""
    return CoverageScore(
        market=Market.HK,
        item=item,
        measurement=CoverageMeasurement(
            covered=covered, denominator=denominator, gap=gap
        ),
    )


def _coverage(*scores: CoverageScore) -> MarketCoverage:
    """A per-market coverage report from the given scores (SP 3.9)."""
    return MarketCoverage(market=Market.HK, scores=scores)


def _thresholds(**overrides: object) -> CoverageThresholdConfig:
    """The documented SP 3.2 threshold config with overridable fields."""
    fields: dict[str, object] = {
        "min_price_coverage_pct": 95.0,
        "min_stock_pool_coverage_pct": 90.0,
        "min_fundamental_coverage_pct": 70.0,
        "fx_required": True,
        "historical_stock_pool_required": True,
        "action_terms_required": True,
    }
    fields.update(overrides)
    return CoverageThresholdConfig(**fields)  # type: ignore[arg-type]


def _evaluate(coverage: MarketCoverage, **overrides: object) -> CoverageGateResult:
    """Run the gate over the coverage report with overridable thresholds."""
    return evaluate_coverage(coverage, _thresholds(**overrides))


class PriceGapTests(unittest.TestCase):
    """价格: a price-coverage breach hard-blocks the run (SP 3.62)."""

    def test_price_below_threshold_blocks(self) -> None:
        result = _evaluate(_coverage(_score(ManifestComponent.PRICES, covered=90, denominator=100)))
        self.assertEqual(result.market, Market.HK)
        threshold = result.results[0]
        self.assertIs(threshold.severity, CoverageSeverity.ERROR)
        self.assertFalse(threshold.passed)
        self.assertIn("below threshold", threshold.reason)
        self.assertTrue(result.blocked)
        self.assertFalse(result.passes)

    def test_price_at_threshold_passes(self) -> None:
        result = _evaluate(
            _coverage(_score(ManifestComponent.PRICES, covered=95, denominator=100))
        )
        self.assertIsNone(result.results[0].severity)
        self.assertFalse(result.blocked)
        self.assertTrue(result.passes)

    def test_full_price_coverage_passes(self) -> None:
        result = _evaluate(
            _coverage(_score(ManifestComponent.PRICES, covered=100, denominator=100))
        )
        self.assertTrue(result.results[0].passed)
        self.assertTrue(result.passes)


class StockPoolGapTests(unittest.TestCase):
    """股票池: an unknown historical pool disqualifies, a breach blocks (SP 3.62)."""

    def test_unknown_history_not_qualified_when_required(self) -> None:
        result = _evaluate(
            _coverage(
                _score(
                    ManifestComponent.STOCK_POOL,
                    covered=95,
                    denominator=100,
                    gap="historical stock pool unknown",
                )
            )
        )
        threshold = result.results[0]
        self.assertIs(threshold.severity, CoverageSeverity.NOT_QUALIFIED)
        self.assertIn("unknown or incomplete", threshold.reason)
        self.assertFalse(result.blocked)
        self.assertFalse(result.passes)

    def test_unknown_history_passes_when_not_required(self) -> None:
        result = _evaluate(
            _coverage(
                _score(
                    ManifestComponent.STOCK_POOL,
                    covered=95,
                    denominator=100,
                    gap="historical stock pool unknown",
                )
            ),
            historical_stock_pool_required=False,
        )
        # Full percentage (>= 90%) and the requirement flag off: no downgrade.
        self.assertIsNone(result.results[0].severity)
        self.assertTrue(result.passes)

    def test_pool_below_threshold_blocks(self) -> None:
        result = _evaluate(
            _coverage(_score(ManifestComponent.STOCK_POOL, covered=80, denominator=100))
        )
        self.assertIs(result.results[0].severity, CoverageSeverity.ERROR)
        self.assertTrue(result.blocked)

    def test_error_wins_over_not_qualified(self) -> None:
        result = _evaluate(
            _coverage(
                _score(
                    ManifestComponent.STOCK_POOL,
                    covered=80,
                    denominator=100,
                    gap="historical stock pool unknown",
                )
            )
        )
        # Both NOT_QUALIFIED (gap + required) and ERROR (pct < 90) apply; the
        # hard error wins the severity precedence.
        self.assertIs(result.results[0].severity, CoverageSeverity.ERROR)
        self.assertTrue(result.blocked)


class FundamentalGapTests(unittest.TestCase):
    """财报可得性: a shortfall only warns and never blocks (SP 3.62)."""

    def test_fundamental_shortfall_warns(self) -> None:
        result = _evaluate(
            _coverage(
                _score(ManifestComponent.FUNDAMENTALS, covered=50, denominator=100)
            )
        )
        self.assertIs(result.results[0].severity, CoverageSeverity.WARNING)
        self.assertFalse(result.blocked)
        self.assertTrue(result.passes)

    def test_fundamental_at_threshold_passes(self) -> None:
        result = _evaluate(
            _coverage(
                _score(ManifestComponent.FUNDAMENTALS, covered=70, denominator=100)
            )
        )
        self.assertTrue(result.results[0].passed)

    def test_fundamental_full_coverage_passes(self) -> None:
        result = _evaluate(
            _coverage(
                _score(ManifestComponent.FUNDAMENTALS, covered=100, denominator=100)
            )
        )
        self.assertTrue(result.results[0].passed)


class CorporateActionGapTests(unittest.TestCase):
    """企业行动: missing terms disqualify when required, else warn (SP 3.62)."""

    def test_missing_terms_not_qualified_when_required(self) -> None:
        result = _evaluate(
            _coverage(
                _score(
                    ManifestComponent.CORPORATE_ACTIONS,
                    covered=60,
                    denominator=100,
                    gap="terms missing",
                )
            )
        )
        self.assertIs(result.results[0].severity, CoverageSeverity.NOT_QUALIFIED)
        self.assertIn("required but missing", result.results[0].reason)
        self.assertFalse(result.passes)

    def test_missing_terms_warns_when_not_required(self) -> None:
        result = _evaluate(
            _coverage(
                _score(
                    ManifestComponent.CORPORATE_ACTIONS,
                    covered=60,
                    denominator=100,
                    gap="terms missing",
                )
            ),
            action_terms_required=False,
        )
        self.assertIs(result.results[0].severity, CoverageSeverity.WARNING)
        self.assertTrue(result.passes)

    def test_full_terms_coverage_passes(self) -> None:
        result = _evaluate(
            _coverage(
                _score(
                    ManifestComponent.CORPORATE_ACTIONS,
                    covered=100,
                    denominator=100,
                )
            )
        )
        self.assertTrue(result.results[0].passed)


class CalendarGapTests(unittest.TestCase):
    """日历: an incomplete calendar only warns (SP 3.62)."""

    def test_calendar_gap_warns(self) -> None:
        result = _evaluate(
            _coverage(_score(ManifestComponent.CALENDAR, covered=80, denominator=100))
        )
        self.assertIs(result.results[0].severity, CoverageSeverity.WARNING)
        self.assertTrue(result.passes)

    def test_calendar_full_coverage_passes(self) -> None:
        result = _evaluate(
            _coverage(_score(ManifestComponent.CALENDAR, covered=100, denominator=100))
        )
        self.assertTrue(result.results[0].passed)


class FxGapTests(unittest.TestCase):
    """FX: missing FX disqualifies when required — never silently passed (SP 3.62)."""

    def test_missing_fx_not_qualified_when_required(self) -> None:
        result = _evaluate(
            _coverage(
                _score(ManifestComponent.FX, covered=0, denominator=100, gap="missing")
            )
        )
        self.assertIs(result.results[0].severity, CoverageSeverity.NOT_QUALIFIED)
        self.assertIn("required but missing", result.results[0].reason)
        self.assertFalse(result.blocked)
        self.assertFalse(result.passes)

    def test_missing_fx_warns_when_not_required(self) -> None:
        result = _evaluate(
            _coverage(
                _score(ManifestComponent.FX, covered=0, denominator=100, gap="missing")
            ),
            fx_required=False,
        )
        self.assertIs(result.results[0].severity, CoverageSeverity.WARNING)
        self.assertTrue(result.passes)

    def test_full_fx_coverage_passes(self) -> None:
        result = _evaluate(
            _coverage(_score(ManifestComponent.FX, covered=100, denominator=100))
        )
        self.assertTrue(result.results[0].passed)


class BenchmarkGapTests(unittest.TestCase):
    """基准: an incomplete benchmark only warns (SP 3.62)."""

    def test_benchmark_gap_warns(self) -> None:
        result = _evaluate(
            _coverage(_score(ManifestComponent.BENCHMARK, covered=90, denominator=100))
        )
        self.assertIs(result.results[0].severity, CoverageSeverity.WARNING)
        self.assertTrue(result.passes)

    def test_benchmark_full_coverage_passes(self) -> None:
        result = _evaluate(
            _coverage(_score(ManifestComponent.BENCHMARK, covered=100, denominator=100))
        )
        self.assertTrue(result.results[0].passed)


class GateAggregationTests(unittest.TestCase):
    """Gate-level blocking vs downgrading (SP 3.62)."""

    _ALL_ITEMS = (
        ManifestComponent.PRICES,
        ManifestComponent.STOCK_POOL,
        ManifestComponent.FUNDAMENTALS,
        ManifestComponent.CORPORATE_ACTIONS,
        ManifestComponent.CALENDAR,
        ManifestComponent.FX,
        ManifestComponent.BENCHMARK,
    )

    def test_all_seven_items_full_coverage_pass(self) -> None:
        coverage = _coverage(
            *(_score(item, covered=100, denominator=100) for item in self._ALL_ITEMS)
        )
        result = _evaluate(coverage)
        self.assertEqual(len(result.results), 7)
        self.assertTrue(all(r.passed for r in result.results))
        self.assertFalse(result.blocked)
        self.assertTrue(result.passes)
        self.assertEqual(result.errors, ())
        self.assertEqual(result.warnings, ())
        self.assertEqual(result.not_qualified_items, ())

    def test_error_blocks_the_run(self) -> None:
        coverage = _coverage(
            _score(ManifestComponent.PRICES, covered=90, denominator=100),
            _score(ManifestComponent.FX, covered=100, denominator=100),
        )
        result = _evaluate(coverage)
        self.assertTrue(result.blocked)
        self.assertFalse(result.passes)
        self.assertEqual(len(result.errors), 1)

    def test_not_qualified_disqualifies_without_blocking(self) -> None:
        coverage = _coverage(
            _score(ManifestComponent.PRICES, covered=100, denominator=100),
            _score(ManifestComponent.FX, covered=0, denominator=100, gap="missing"),
        )
        result = _evaluate(coverage)
        self.assertFalse(result.blocked)
        self.assertFalse(result.passes)
        self.assertEqual(len(result.not_qualified_items), 1)
        self.assertEqual(result.errors, ())

    def test_warnings_alone_still_pass(self) -> None:
        coverage = _coverage(
            _score(ManifestComponent.FUNDAMENTALS, covered=50, denominator=100),
            _score(ManifestComponent.CALENDAR, covered=80, denominator=100),
            _score(ManifestComponent.BENCHMARK, covered=90, denominator=100),
        )
        result = _evaluate(coverage)
        self.assertFalse(result.blocked)
        self.assertTrue(result.passes)
        self.assertEqual(len(result.warnings), 3)
        self.assertEqual(result.errors, ())
        self.assertEqual(result.not_qualified_items, ())

    def test_mixed_gate_aggregation(self) -> None:
        coverage = _coverage(
            _score(ManifestComponent.PRICES, covered=100, denominator=100),
            _score(
                ManifestComponent.STOCK_POOL,
                covered=95,
                denominator=100,
                gap="historical stock pool unknown",
            ),
            _score(ManifestComponent.FUNDAMENTALS, covered=50, denominator=100),
        )
        result = _evaluate(coverage)
        self.assertFalse(result.blocked)
        self.assertFalse(result.passes)
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(len(result.not_qualified_items), 1)
        self.assertEqual(result.errors, ())

    def test_readable(self) -> None:
        coverage = _coverage(
            _score(ManifestComponent.PRICES, covered=90, denominator=100),
            _score(ManifestComponent.FX, covered=100, denominator=100),
        )
        text = _evaluate(coverage).readable()
        self.assertIn("HK", text)
        self.assertIn("passes=False", text)
        self.assertIn("blocked=True", text)
        self.assertIn("errors 1", text)

    def test_threshold_result_readable(self) -> None:
        threshold = _evaluate(
            _coverage(_score(ManifestComponent.PRICES, covered=90, denominator=100))
        ).results[0]
        self.assertIn("HK/prices", threshold.readable())
        self.assertIn("ERROR", threshold.readable())


if __name__ == "__main__":
    unittest.main()
