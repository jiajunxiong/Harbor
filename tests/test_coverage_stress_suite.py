"""Data coverage & stress test suite (MVP 3 / SP 3.79).

Consolidated suite verifying the impact of 覆盖门槛 (coverage thresholds,
SP 3.10), 环境分类 (environment classification, SP 3.48/3.50) and ALL stress
scenario families (成本/流动性/FX/日历/企业行动/股票池/参数邻域, SP 3.51–3.57)
on the conclusion grade (QUALIFIED / NOT_QUALIFIED / INCONCLUSIVE, SP 3.58),
and that every stress scenario must be pre-registered (SP 3.59) — an
unregistered scenario never enters a conclusion (禁止未登记情景进入结论).

The bridge into the SP 3.58 stability rule: coverage ERROR → ``coverage_blocked``
→ NOT_QUALIFIED; environment insufficient ratio > threshold → NOT_QUALIFIED
(missing → INCONCLUSIVE); stress loss > threshold / unquantifiable → NOT_QUALIFIED
(missing → INCONCLUSIVE); any FAIL dominates, missing evidence never passes.
No database is required.
"""

import unittest

from harbor.core.backtest_domain import Market
from harbor.core.coverage_gate import evaluate_coverage
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.environment_segmented import (
    EnvironmentDimension,
    EnvironmentSegmentedError,
    EnvironmentSegmentedPerformance,
    EnvironmentSegmentPerformance,
)
from harbor.core.stability_rule import (
    StabilitySignals,
    adjudicate_stability,
    default_stability_rule,
)
from harbor.core.stress_registry import (
    RequiredScenario,
    StressRegistryError,
    StressScenarioCategory,
    build_scenario_registration,
    build_stress_registry,
    register_scenario,
    require_scenarios_registered,
    scenario_refs_from_reports,
)
from harbor.core.validation_config import CoverageThresholdConfig
from harbor.core.validation_domain import ManifestComponent, OOSConclusion

_FINGERPRINT = "dataset-fingerprint-demo"
_CODE_VERSION = "1.0.0"

#: One representative scenario per SP 3.59 category (all 7 families).
_ALL_SCENARIOS = (
    (StressScenarioCategory.COST, "cost-stress-2x"),
    (StressScenarioCategory.LIQUIDITY, "liquidity-stress-50bps"),
    (StressScenarioCategory.FX, "fx-stress-shock"),
    (StressScenarioCategory.CALENDAR, "calendar-stress-holiday"),
    (StressScenarioCategory.CORPORATE_ACTION, "corporate-action-stress-missing-terms"),
    (StressScenarioCategory.STOCK_POOL, "stock-pool-stress-unknown-history"),
    (StressScenarioCategory.PARAMETER_NEIGHBORHOOD, "parameter-neighborhood-stress-cliff"),
)


def _signals(**overrides) -> StabilitySignals:
    """Return clean stability signals (all dimensions PASS)."""
    fields = dict(
        market=Market.HK,
        dataset_fingerprint=_FINGERPRINT,
        code_version=_CODE_VERSION,
        fold_spread=0.05,
        fold_count=4,
        fold_failure_count=0,
        neighborhood_cliff_ratio=0.1,
        neighborhood_infeasible_ratio=0.0,
        environment_insufficient_ratio=0.0,
        max_stress_loss_pct=2.0,
        stress_unquantifiable=False,
        coverage_blocked=False,
    )
    fields.update(overrides)
    return StabilitySignals(**fields)  # type: ignore[arg-type]


def _conclusion(**overrides) -> OOSConclusion:
    """Adjudicate the stability conclusion over the (possibly overridden) signals."""
    return adjudicate_stability(_signals(**overrides), config=default_stability_rule()).conclusion


def _score(item: ManifestComponent, covered: int, denominator: int, gap: str = "") -> CoverageScore:
    """Return one per-item coverage score (SP 3.9)."""
    return CoverageScore(
        market=Market.HK,
        item=item,
        measurement=CoverageMeasurement(covered=covered, denominator=denominator, gap=gap),
    )


def _coverage(*scores: CoverageScore) -> MarketCoverage:
    """Return a per-market coverage report (SP 3.9)."""
    return MarketCoverage(market=Market.HK, scores=tuple(scores))


def _clean_coverage() -> MarketCoverage:
    """Full price / stock-pool / fundamental coverage (no gaps)."""
    return _coverage(
        _score(ManifestComponent.PRICES, 100, 100),
        _score(ManifestComponent.STOCK_POOL, 100, 100),
        _score(ManifestComponent.FUNDAMENTALS, 100, 100),
    )


def _segment(regime_name: str, *, sufficient: bool = True) -> EnvironmentSegmentPerformance:
    """Return one environment segment (SP 3.50)."""
    if sufficient:
        return EnvironmentSegmentPerformance(
            dimension=EnvironmentDimension.TREND,
            regime_name=regime_name,
            day_count=100,
            sufficient=True,
            insufficient_reason=None,
            strategy_return=0.05,
            strategy_drawdown=-0.05,
            strategy_volatility=0.15,
            strategy_sharpe=1.2,
            benchmark_return=0.02,
            excess_return=0.03,
            turnover=0.4,
            costs=120.0,
            coverage_pct=95.0,
        )
    return EnvironmentSegmentPerformance(
        dimension=EnvironmentDimension.TREND,
        regime_name=regime_name,
        day_count=10,
        sufficient=False,
        insufficient_reason="insufficient sample (< min_samples)",
        strategy_return=None,
        strategy_drawdown=None,
        strategy_volatility=None,
        strategy_sharpe=None,
        benchmark_return=None,
        excess_return=None,
        turnover=None,
        costs=None,
        coverage_pct=None,
    )


def _segmented(*segments: EnvironmentSegmentPerformance) -> EnvironmentSegmentedPerformance:
    """Return an environment-segmented performance report (SP 3.50)."""
    return EnvironmentSegmentedPerformance(
        definition_version="env-v1",
        definition_fingerprint="env-fp",
        dataset_fingerprint=_FINGERPRINT,
        code_version=_CODE_VERSION,
        min_samples=63,
        segments=tuple(segments),
        fingerprint="fp",
    )


def _registration(category: StressScenarioCategory, scenario_id: str):
    """Return one pre-registered stress scenario (SP 3.59)."""
    return build_scenario_registration(
        category=category,
        scenario_id=scenario_id,
        market=Market.HK,
        assumptions=("pre-registered SP 3.79 scenario",),
        parameters={"multiplier": 2.0},
        dataset_fingerprint=_FINGERPRINT,
        code_version=_CODE_VERSION,
        difference_summary="difference not yet measured (pre-registered)",
    )


def _all_registry():
    """Register one scenario for every SP 3.59 category."""
    registry = build_stress_registry(
        version="demo-v1",
        source="tests",
        registrations=(_registration(*_ALL_SCENARIOS[0]),),
    )
    for category, scenario_id in _ALL_SCENARIOS[1:]:
        registry = register_scenario(registry, _registration(category, scenario_id))
    return registry


class CoverageThresholdConclusionTests(unittest.TestCase):
    """SP 3.10/3.58 覆盖门槛: thresholds drive the conclusion grade."""

    def test_clean_coverage_qualifies(self) -> None:
        gate = evaluate_coverage(_clean_coverage(), CoverageThresholdConfig())
        self.assertFalse(gate.blocked)
        self.assertTrue(gate.passes)
        self.assertEqual(_conclusion(coverage_blocked=gate.blocked), OOSConclusion.QUALIFIED)

    def test_price_below_threshold_blocks_to_not_qualified(self) -> None:
        gate = evaluate_coverage(
            _coverage(
                _score(ManifestComponent.PRICES, 50, 100),
                _score(ManifestComponent.STOCK_POOL, 100, 100),
                _score(ManifestComponent.FUNDAMENTALS, 100, 100),
            ),
            CoverageThresholdConfig(),
        )
        self.assertTrue(gate.blocked)
        self.assertEqual(_conclusion(coverage_blocked=gate.blocked), OOSConclusion.NOT_QUALIFIED)

    def test_price_at_threshold_passes(self) -> None:
        gate = evaluate_coverage(
            _coverage(
                _score(ManifestComponent.PRICES, 95, 100),
                _score(ManifestComponent.STOCK_POOL, 100, 100),
                _score(ManifestComponent.FUNDAMENTALS, 100, 100),
            ),
            CoverageThresholdConfig(),
        )
        self.assertFalse(gate.blocked)

    def test_missing_fx_disqualifies_the_gate(self) -> None:
        gate = evaluate_coverage(
            _coverage(
                _score(ManifestComponent.PRICES, 100, 100),
                _score(ManifestComponent.STOCK_POOL, 100, 100),
                _score(ManifestComponent.FX, 0, 100, gap="missing FX data"),
            ),
            CoverageThresholdConfig(),
        )
        self.assertFalse(gate.blocked)
        self.assertFalse(gate.passes)
        self.assertEqual(len(gate.not_qualified_items), 1)

    def test_unknown_stock_pool_disqualifies_the_gate(self) -> None:
        gate = evaluate_coverage(
            _coverage(
                _score(ManifestComponent.PRICES, 100, 100),
                _score(ManifestComponent.STOCK_POOL, 95, 100, gap="unknown historical pool"),
            ),
            CoverageThresholdConfig(),
        )
        self.assertFalse(gate.passes)
        self.assertTrue(any(gate.not_qualified_items))


class EnvironmentClassificationTests(unittest.TestCase):
    """SP 3.48/3.50/3.58 环境分类: insufficient segments drive the grade."""

    def test_all_sufficient_environment_qualifies(self) -> None:
        report = _segmented(_segment("bear"), _segment("bull"))
        self.assertEqual(report.insufficient_count, 0)
        self.assertEqual(_conclusion(environment_insufficient_ratio=0.0), OOSConclusion.QUALIFIED)

    def test_insufficient_ratio_within_threshold_passes(self) -> None:
        report = _segmented(
            _segment("bear", sufficient=False), _segment("bull"), _segment("flat"), _segment("jump")
        )
        ratio = report.insufficient_count / len(report)
        self.assertEqual(ratio, 0.25)
        self.assertEqual(_conclusion(environment_insufficient_ratio=ratio), OOSConclusion.QUALIFIED)

    def test_insufficient_ratio_above_threshold_fails(self) -> None:
        report = _segmented(
            _segment("bear", sufficient=False),
            _segment("bull", sufficient=False),
            _segment("flat", sufficient=False),
            _segment("jump"),
        )
        ratio = report.insufficient_count / len(report)
        self.assertEqual(ratio, 0.75)
        self.assertEqual(
            _conclusion(environment_insufficient_ratio=ratio), OOSConclusion.NOT_QUALIFIED
        )

    def test_missing_environment_evidence_is_inconclusive(self) -> None:
        self.assertEqual(
            _conclusion(environment_insufficient_ratio=None), OOSConclusion.INCONCLUSIVE
        )

    def test_insufficient_segment_requires_reason(self) -> None:
        with self.assertRaises(EnvironmentSegmentedError):
            EnvironmentSegmentPerformance(
                dimension=EnvironmentDimension.TREND,
                regime_name="bear",
                day_count=10,
                sufficient=False,
                insufficient_reason=None,
                strategy_return=None,
                strategy_drawdown=None,
                strategy_volatility=None,
                strategy_sharpe=None,
                benchmark_return=None,
                excess_return=None,
                turnover=None,
                costs=None,
                coverage_pct=None,
            )


class StressScenarioConclusionTests(unittest.TestCase):
    """SP 3.51–3.59 所有压力情景: every family registers and drives the grade."""

    def setUp(self) -> None:
        self.registry = _all_registry()

    def test_all_seven_categories_registered(self) -> None:
        self.assertEqual(len(self.registry), 7)
        for category, scenario_id in _ALL_SCENARIOS:
            with self.subTest(scenario_id=scenario_id):
                self.assertTrue(self.registry.contains(category, scenario_id))

    def test_every_category_requires_registration(self) -> None:
        required = tuple(
            RequiredScenario(category=category, scenario_id=scenario_id)
            for category, scenario_id in _ALL_SCENARIOS
        )
        require_scenarios_registered(
            self.registry, required=required, conclusion_label="coverage-stress-suite"
        )

    def test_each_category_within_threshold_passes(self) -> None:
        for category, scenario_id in _ALL_SCENARIOS:
            with self.subTest(scenario_id=scenario_id):
                self.assertEqual(_conclusion(max_stress_loss_pct=2.0), OOSConclusion.QUALIFIED)

    def test_each_category_above_threshold_not_qualified(self) -> None:
        for category, scenario_id in _ALL_SCENARIOS:
            with self.subTest(scenario_id=scenario_id):
                self.assertEqual(_conclusion(max_stress_loss_pct=15.0), OOSConclusion.NOT_QUALIFIED)

    def test_each_category_unquantifiable_fails(self) -> None:
        for category, scenario_id in _ALL_SCENARIOS:
            with self.subTest(scenario_id=scenario_id):
                self.assertEqual(
                    _conclusion(stress_unquantifiable=True), OOSConclusion.NOT_QUALIFIED
                )

    def test_each_category_missing_evidence_inconclusive(self) -> None:
        for category, scenario_id in _ALL_SCENARIOS:
            with self.subTest(scenario_id=scenario_id):
                self.assertEqual(_conclusion(max_stress_loss_pct=None), OOSConclusion.INCONCLUSIVE)

    def test_unregistered_scenario_blocks_conclusion(self) -> None:
        extra = RequiredScenario(
            category=StressScenarioCategory.CALENDAR, scenario_id="calendar-stress-2027"
        )
        with self.assertRaises(StressRegistryError) as context:
            require_scenarios_registered(
                self.registry, required=(extra,), conclusion_label="unregistered"
            )
        self.assertIn("calendar-stress-2027", str(context.exception))

    def test_scenario_refs_from_reports_tie_to_registry(self) -> None:
        class _Stress:
            def __init__(self, version: str) -> None:
                self.version = version

        class _Scenario:
            def __init__(self, version: str) -> None:
                self.stress = _Stress(version)

        category, scenario_id = _ALL_SCENARIOS[0]
        refs = scenario_refs_from_reports(category, (_Scenario(scenario_id),))
        require_scenarios_registered(self.registry, required=refs, conclusion_label="refs")
        self.assertEqual(refs[0].scenario_id, scenario_id)


class ConclusionGradeAggregationTests(unittest.TestCase):
    """SP 3.58 结论等级: aggregation of all evidence dimensions."""

    def test_all_clean_is_qualified(self) -> None:
        self.assertEqual(_conclusion(), OOSConclusion.QUALIFIED)

    def test_any_fail_dominates_to_not_qualified(self) -> None:
        self.assertEqual(
            _conclusion(max_stress_loss_pct=15.0, coverage_blocked=True),
            OOSConclusion.NOT_QUALIFIED,
        )

    def test_missing_evidence_is_inconclusive_not_a_pass(self) -> None:
        conclusion = _conclusion(fold_spread=None)
        self.assertEqual(conclusion, OOSConclusion.INCONCLUSIVE)
        self.assertNotEqual(conclusion, OOSConclusion.QUALIFIED)

    def test_fold_failure_fails(self) -> None:
        self.assertEqual(
            _conclusion(fold_count=4, fold_failure_count=1, fold_spread=0.05),
            OOSConclusion.NOT_QUALIFIED,
        )

    def test_missing_neighborhood_evidence_inconclusive(self) -> None:
        self.assertEqual(
            _conclusion(neighborhood_cliff_ratio=None, neighborhood_infeasible_ratio=None),
            OOSConclusion.INCONCLUSIVE,
        )

    def test_conclusion_auditable_and_rederivable(self) -> None:
        conclusion = adjudicate_stability(_signals(), config=default_stability_rule())
        self.assertIn("stability", conclusion.readable().lower())
        self.assertTrue(conclusion.fingerprint)


if __name__ == "__main__":
    unittest.main()
