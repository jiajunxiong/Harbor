"""Coverage thresholds and blocking rules tests (MVP 3 / SP 3.10).

Verifies that configured coverage thresholds turn a per-market coverage score
into per-item decisions: percentage breaches become hard ``ERROR`` (price,
stock pool), fundamental shortfalls, calendar and benchmark gaps become
``WARNING``, and missing FX / an unknown stock pool / missing corporate-action
terms become ``NOT_QUALIFIED`` (or ``WARNING`` when the requirement flag is
off) — never silently passed.
"""

import unittest
from dataclasses import FrozenInstanceError

from harbor.core.backtest_domain import Market
from harbor.core.coverage_gate import (
    CoverageGateResult,
    CoverageThresholdResult,
    evaluate_coverage,
)
from harbor.core.coverage_scoring import (
    SCORED_ITEMS,
    CoverageMeasurement,
    MarketCoverage,
    score_market_coverage,
)
from harbor.core.validation_config import CoverageSeverity, CoverageThresholdConfig
from harbor.core.validation_domain import ManifestComponent


def _coverage(measurements: dict[ManifestComponent, CoverageMeasurement]) -> MarketCoverage:
    """Score HK with only the given measurement items."""
    return score_market_coverage(Market.HK, measurements, scored=tuple(measurements))


def _full() -> MarketCoverage:
    """Score HK with every scored item at full coverage."""
    return _coverage({item: CoverageMeasurement(100, 100) for item in SCORED_ITEMS})


def _gate(
    coverage: MarketCoverage | None = None,
    thresholds: CoverageThresholdConfig | None = None,
) -> CoverageGateResult:
    return evaluate_coverage(
        coverage if coverage is not None else _full(),
        thresholds if thresholds is not None else CoverageThresholdConfig(),
    )


class CoverageThresholdResultTests(unittest.TestCase):
    """Verify a single per-item decision."""

    def test_clean_item_passes(self) -> None:
        result = CoverageThresholdResult(
            market=Market.HK,
            item=ManifestComponent.PRICES,
            coverage_pct=100.0,
        )
        self.assertTrue(result.passed)
        self.assertIsNone(result.severity)
        self.assertIn("passed", result.readable())

    def test_gap_item_carries_severity_and_reason(self) -> None:
        result = CoverageThresholdResult(
            market=Market.HK,
            item=ManifestComponent.FX,
            coverage_pct=0.0,
            severity=CoverageSeverity.NOT_QUALIFIED,
            reason="FX data is required but missing or incomplete",
        )
        self.assertFalse(result.passed)
        self.assertIn("not_qualified", result.readable())
        self.assertIn("missing or incomplete", result.readable())

    def test_is_frozen(self) -> None:
        result = CoverageThresholdResult(
            market=Market.HK,
            item=ManifestComponent.PRICES,
            coverage_pct=100.0,
        )
        with self.assertRaises(FrozenInstanceError):
            result.coverage_pct = 50.0  # type: ignore[misc]


class CoverageGateResultTests(unittest.TestCase):
    """Verify the aggregate gate outcome."""

    def _mixed(self) -> CoverageGateResult:
        return _gate(
            _coverage(
                {
                    ManifestComponent.PRICES: CoverageMeasurement(94, 100),
                    ManifestComponent.FX: CoverageMeasurement(0, 1, gap="USD rate missing"),
                    ManifestComponent.FUNDAMENTALS: CoverageMeasurement(60, 100),
                }
            )
        )

    def test_partitions_results_by_severity(self) -> None:
        gate = self._mixed()
        self.assertEqual(len(gate.errors), 1)
        self.assertIs(gate.errors[0].item, ManifestComponent.PRICES)
        self.assertEqual(len(gate.not_qualified_items), 1)
        self.assertIs(gate.not_qualified_items[0].item, ManifestComponent.FX)
        self.assertEqual(len(gate.warnings), 1)
        self.assertIs(gate.warnings[0].item, ManifestComponent.FUNDAMENTALS)

    def test_blocked_when_any_error(self) -> None:
        self.assertTrue(self._mixed().blocked)

    def test_not_passing_with_not_qualified(self) -> None:
        self.assertFalse(self._mixed().passes)

    def test_clean_gate_passes(self) -> None:
        gate = _gate()
        self.assertTrue(gate.passes)
        self.assertFalse(gate.blocked)
        self.assertEqual(gate.errors, ())
        self.assertEqual(gate.warnings, ())

    def test_warning_only_gate_still_passes(self) -> None:
        gate = _gate(_coverage({ManifestComponent.FUNDAMENTALS: CoverageMeasurement(60, 100)}))
        self.assertTrue(gate.passes)
        self.assertFalse(gate.blocked)
        self.assertEqual(len(gate.warnings), 1)

    def test_requires_at_least_one_result(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one result"):
            CoverageGateResult(market=Market.HK, results=())

    def test_readable_single_line(self) -> None:
        self.assertIn("passes=True", _gate().readable())


class EvaluateCoverageTests(unittest.TestCase):
    """Verify the threshold evaluation rules (SP 3.10)."""

    def test_price_below_min_is_error(self) -> None:
        gate = _gate(_coverage({ManifestComponent.PRICES: CoverageMeasurement(94, 100)}))
        self.assertTrue(gate.blocked)
        self.assertIs(gate.errors[0].severity, CoverageSeverity.ERROR)

    def test_price_at_threshold_passes(self) -> None:
        gate = _gate(_coverage({ManifestComponent.PRICES: CoverageMeasurement(95, 100)}))
        self.assertTrue(gate.passes)
        self.assertEqual(gate.errors, ())

    def test_fundamentals_below_min_is_warning(self) -> None:
        gate = _gate(_coverage({ManifestComponent.FUNDAMENTALS: CoverageMeasurement(60, 100)}))
        self.assertEqual(len(gate.warnings), 1)
        self.assertFalse(gate.blocked)
        self.assertTrue(gate.passes)

    def test_fx_missing_when_required_is_not_qualified(self) -> None:
        gate = _gate(_coverage({ManifestComponent.FX: CoverageMeasurement(0, 1, gap="missing")}))
        self.assertEqual(len(gate.not_qualified_items), 1)
        self.assertFalse(gate.passes)
        self.assertIs(gate.not_qualified_items[0].severity, CoverageSeverity.NOT_QUALIFIED)

    def test_fx_missing_when_not_required_is_warning(self) -> None:
        thresholds = CoverageThresholdConfig(fx_required=False)
        gate = _gate(
            _coverage({ManifestComponent.FX: CoverageMeasurement(0, 1, gap="missing")}),
            thresholds,
        )
        self.assertEqual(len(gate.warnings), 1)
        self.assertTrue(gate.passes)

    def test_unknown_stock_pool_when_required_is_not_qualified(self) -> None:
        # An explicit pool gap above the 90% minimum is NOT_QUALIFIED, not an error.
        gate = _gate(
            _coverage(
                {ManifestComponent.STOCK_POOL: CoverageMeasurement(95, 100, gap="unknown pool")}
            )
        )
        self.assertEqual(len(gate.not_qualified_items), 1)
        self.assertFalse(gate.passes)

    def test_stock_pool_below_min_is_error(self) -> None:
        gate = _gate(_coverage({ManifestComponent.STOCK_POOL: CoverageMeasurement(85, 100)}))
        self.assertTrue(gate.blocked)
        self.assertIs(gate.errors[0].severity, CoverageSeverity.ERROR)

    def test_missing_corporate_action_terms_when_required_is_not_qualified(self) -> None:
        gate = _gate(
            _coverage(
                {
                    ManifestComponent.CORPORATE_ACTIONS: CoverageMeasurement(
                        0, 1, gap="terms missing"
                    )
                }
            )
        )
        self.assertEqual(len(gate.not_qualified_items), 1)
        self.assertFalse(gate.passes)

    def test_missing_corporate_action_terms_when_not_required_is_warning(self) -> None:
        thresholds = CoverageThresholdConfig(action_terms_required=False)
        gate = _gate(
            _coverage(
                {
                    ManifestComponent.CORPORATE_ACTIONS: CoverageMeasurement(
                        0, 1, gap="terms missing"
                    )
                }
            ),
            thresholds,
        )
        self.assertEqual(len(gate.warnings), 1)
        self.assertTrue(gate.passes)

    def test_calendar_gap_is_warning(self) -> None:
        gate = _gate(
            _coverage({ManifestComponent.CALENDAR: CoverageMeasurement(0, 1, gap="holidays")})
        )
        self.assertEqual(len(gate.warnings), 1)
        self.assertTrue(gate.passes)

    def test_benchmark_gap_is_warning(self) -> None:
        gate = _gate(
            _coverage({ManifestComponent.BENCHMARK: CoverageMeasurement(0, 1, gap="none")})
        )
        self.assertEqual(len(gate.warnings), 1)
        self.assertTrue(gate.passes)

    def test_error_takes_precedence_over_not_qualified(self) -> None:
        # An unknown pool that is also below the minimum resolves to ERROR.
        gate = _gate(
            _coverage({ManifestComponent.STOCK_POOL: CoverageMeasurement(0, 1, gap="unknown")})
        )
        self.assertTrue(gate.blocked)
        self.assertIs(gate.errors[0].severity, CoverageSeverity.ERROR)

    def test_combined_errors_and_not_qualified_block(self) -> None:
        gate = _gate(
            _coverage(
                {
                    ManifestComponent.PRICES: CoverageMeasurement(80, 100),
                    ManifestComponent.FX: CoverageMeasurement(0, 1, gap="missing"),
                }
            )
        )
        self.assertTrue(gate.blocked)
        self.assertFalse(gate.passes)


if __name__ == "__main__":
    unittest.main()
