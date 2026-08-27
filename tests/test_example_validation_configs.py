"""Example validation configuration tests (MVP 3 / SP 3.72).

Verifies the shipped example validation configurations (small-scale HK, US and
cross-market mocks) load through the SP 3.3 loader, explicitly demonstrate the
four SP 3.72 dimensions — frozen split (冻结切分), trial budget (试验预算),
coverage thresholds (覆盖门槛) and stress scenarios (压力情景) — and tie to the
deps: SP 3.3 config hash (frozen split), SP 3.30 pre-registered baseline
(fixed before search) and SP 3.59 stress-scenario registration (unregistered
scenarios never enter a conclusion). No database is required.
"""

import unittest
from datetime import date
from pathlib import Path

from harbor.core.backtest_domain import Currency, Market
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.pre_registered_baseline import (
    baseline_fingerprint,
    compare_baseline_selection,
    pre_register_baseline,
)
from harbor.core.stress_registry import (
    RequiredScenario,
    StressScenarioCategory,
    build_scenario_registration,
    build_stress_registry,
    check_scenarios_registered,
    register_scenario,
    require_scenarios_registered,
)
from harbor.core.validation_config import MetricDirection
from harbor.core.validation_config_loader import config_hash, load_validation_config
from harbor.core.validation_domain import Parameter, ParameterTrial

_EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "configs" / "validation"

_HK = "hk_validation.yaml"
_US = "us_validation.yaml"
_US_JSON = "us_validation.json"
_CROSS = "cross_market_validation.yaml"
_ALL = (_HK, _US, _CROSS, _US_JSON)

_DEMO_DATASET_FINGERPRINT = "demo-dataset-fingerprint"
_DEMO_BASELINE_METRIC = 0.10


def _load(name: str):
    """Load one shipped example validation configuration (SP 3.3)."""
    return load_validation_config(_EXAMPLES_DIR / name)


def _category_for(name: str) -> StressScenarioCategory:
    """Map a documented scenario name prefix to its SP 3.59 category.

    The naming convention is documented in examples/configs/validation/README.md
    (cost-/liquidity-/fx-/calendar-/corporate-action-/stock-pool-/
    parameter-neighborhood-).
    """
    prefixes = {
        "cost-": StressScenarioCategory.COST,
        "liquidity-": StressScenarioCategory.LIQUIDITY,
        "fx-": StressScenarioCategory.FX,
        "calendar-": StressScenarioCategory.CALENDAR,
        "corporate-action-": StressScenarioCategory.CORPORATE_ACTION,
        "stock-pool-": StressScenarioCategory.STOCK_POOL,
        "parameter-neighborhood-": StressScenarioCategory.PARAMETER_NEIGHBORHOOD,
    }
    for prefix, category in prefixes.items():
        if name.startswith(prefix):
            return category
    raise ValueError(f"no documented SP 3.59 category for stress scenario {name!r}")


def _registration_for(config, scenario):
    """Build one SP 3.59 registration from a config stress scenario (pre-registered)."""
    market = config.markets[0] if len(config.markets) == 1 else None
    return build_scenario_registration(
        category=_category_for(scenario.name),
        scenario_id=scenario.name,
        market=market,
        assumptions=("pre-registered SP 3.72 example stress scenario",),
        parameters={
            "cost_multiplier": scenario.cost_multiplier,
            "slippage_bps": scenario.slippage_bps,
            "participation_rate": scenario.participation_rate,
            "fx_shift_bps": scenario.fx_shift_bps,
        },
        dataset_fingerprint=_DEMO_DATASET_FINGERPRINT,
        code_version=config.code_version,
        difference_summary="difference not yet measured (pre-registered)",
    )


def _registry_for(config):
    """Register every stress scenario of a config into an SP 3.59 registry."""
    registrations = [_registration_for(config, scenario) for scenario in config.stress]
    registry = build_stress_registry(
        version="demo-v1",
        source="examples/configs/validation",
        registrations=(registrations[0],),
    )
    for registration in registrations[1:]:
        registry = register_scenario(registry, registration)
    return registry


class HkValidationExampleTests(unittest.TestCase):
    """Verify the HK small-mock validation example (SP 3.72)."""

    def setUp(self) -> None:
        self.config = _load(_HK)

    def test_loads_as_hk_small_mock(self) -> None:
        self.assertEqual(self.config.markets, (Market.HK,))
        self.assertEqual(self.config.base_currency, Currency.HKD)
        self.assertEqual(self.config.data_cutoff, date(2022, 12, 31))

    def test_frozen_split_explicit(self) -> None:
        split = self.config.split.to_evaluation_split()
        self.assertEqual(split.train_start, date(2019, 1, 1))
        self.assertEqual(split.test_end, date(2022, 12, 31))
        self.assertGreater(split.train_days, 0)
        self.assertGreater(split.validation_days, 0)
        self.assertGreater(split.test_days, 0)
        self.assertLessEqual(self.config.split.test_end, self.config.data_cutoff)

    def test_trial_budget_explicit(self) -> None:
        self.assertGreater(self.config.tuning.max_trials, 0)
        self.assertEqual(self.config.tuning.random_seed, 42)
        self.assertTrue(self.config.tuning.primary_metric.strip())

    def test_coverage_thresholds_explicit(self) -> None:
        for pct in (
            self.config.coverage.min_price_coverage_pct,
            self.config.coverage.min_stock_pool_coverage_pct,
            self.config.coverage.min_fundamental_coverage_pct,
        ):
            self.assertGreaterEqual(pct, 0.0)
            self.assertLessEqual(pct, 100.0)
        self.assertTrue(self.config.coverage.fx_required)
        self.assertTrue(self.config.coverage.historical_stock_pool_required)
        self.assertTrue(self.config.coverage.action_terms_required)

    def test_stress_scenarios_explicit(self) -> None:
        self.assertEqual(
            [scenario.name for scenario in self.config.stress],
            ["cost-stress-2x", "liquidity-stress-50bps"],
        )
        self.assertGreater(self.config.stress[0].cost_multiplier, 1.0)

    def test_research_purpose_documented(self) -> None:
        self.assertIn("research", self.config.description.lower())


class UsValidationExampleTests(unittest.TestCase):
    """Verify the US small-mock validation example and its JSON twin (SP 3.72)."""

    def setUp(self) -> None:
        self.config = _load(_US)

    def test_loads_as_us_small_mock(self) -> None:
        self.assertEqual(self.config.markets, (Market.US,))
        self.assertEqual(self.config.base_currency, Currency.USD)
        self.assertGreater(self.config.tuning.max_trials, 0)
        self.assertTrue(self.config.coverage.fx_required)

    def test_json_equivalent_to_yaml(self) -> None:
        self.assertEqual(config_hash(_load(_US)), config_hash(_load(_US_JSON)))

    def test_research_purpose_documented(self) -> None:
        self.assertIn("research", self.config.description.lower())


class CrossMarketValidationExampleTests(unittest.TestCase):
    """Verify the cross-market (HK+US) validation example (SP 3.72)."""

    def setUp(self) -> None:
        self.config = _load(_CROSS)

    def test_loads_hk_and_us_with_hkd_base(self) -> None:
        self.assertEqual(self.config.markets, (Market.HK, Market.US))
        self.assertEqual(self.config.base_currency, Currency.HKD)
        self.assertLessEqual(self.config.split.test_end, self.config.data_cutoff)

    def test_fx_stress_demonstrated(self) -> None:
        fx_scenario = next(
            scenario for scenario in self.config.stress if scenario.name == "fx-stress-usd"
        )
        self.assertEqual(_category_for(fx_scenario.name), StressScenarioCategory.FX)
        self.assertLess(fx_scenario.fx_shift_bps, 0)

    def test_research_purpose_documented(self) -> None:
        self.assertIn("research", self.config.description.lower())


class FrozenSplitDemonstrationTests(unittest.TestCase):
    """SP 3.3 tie: the examples carry a stable, distinct frozen-split hash."""

    def test_every_split_is_valid_and_frozen(self) -> None:
        for name in (_HK, _US, _CROSS):
            with self.subTest(name=name):
                config = _load(name)
                split = config.split.to_evaluation_split()
                self.assertGreater(split.train_days, 0, name)
                self.assertGreater(split.validation_days, 0, name)
                self.assertGreater(split.test_days, 0, name)
                self.assertLessEqual(config.split.test_end, config.data_cutoff, name)

    def test_config_hash_stable_across_loads(self) -> None:
        self.assertEqual(config_hash(_load(_HK)), config_hash(_load(_HK)))

    def test_examples_hash_distinctly(self) -> None:
        hashes = {config_hash(_load(name)) for name in (_HK, _US, _CROSS)}
        self.assertEqual(len(hashes), 3)

    def test_json_twin_hash_equal(self) -> None:
        self.assertEqual(config_hash(_load(_US)), config_hash(_load(_US_JSON)))


class TrialBudgetDemonstrationTests(unittest.TestCase):
    """SP 3.30 tie: every example fixes its trial budget and a baseline before search."""

    def test_every_example_declares_a_budget(self) -> None:
        for name in (_HK, _US, _CROSS):
            with self.subTest(name=name):
                config = _load(name)
                self.assertGreater(config.tuning.max_trials, 0, name)
                self.assertIsNotNone(config.tuning.random_seed, name)
                self.assertTrue(config.tuning.primary_metric.strip(), name)

    def test_pre_registered_baseline_builds(self) -> None:
        for name in (_HK, _US, _CROSS):
            with self.subTest(name=name):
                config = _load(name)
                baseline = self._baseline(config)
                self.assertEqual(baseline.metric_name, config.tuning.primary_metric, name)
                self.assertEqual(
                    [parameter.name for parameter in baseline.parameters], ["cash_weight"], name
                )
                self.assertEqual(baseline.fingerprint, baseline_fingerprint(baseline), name)

    def test_baseline_is_fixed_before_search(self) -> None:
        config = _load(_HK)
        baseline = self._baseline(config)
        better = self._selected_trial(config, cash_weight=0.10, metric=0.12)
        comparison = compare_baseline_selection(
            baseline, better, direction=MetricDirection.HIGHER_BETTER
        )
        self.assertAlmostEqual(comparison.metric_gap, 0.02, places=6)
        self.assertTrue(comparison.improved)
        # A lower selected metric never beats the fixed baseline: the baseline is
        # not silently adjusted after the search.
        worse = self._selected_trial(config, cash_weight=0.10, metric=0.08)
        comparison = compare_baseline_selection(
            baseline, worse, direction=MetricDirection.HIGHER_BETTER
        )
        self.assertAlmostEqual(comparison.metric_gap, -0.02, places=6)
        self.assertFalse(comparison.improved)

    def test_example_documents_the_baseline(self) -> None:
        for name in (_HK, _US, _CROSS):
            with self.subTest(name=name):
                text = (_EXAMPLES_DIR / name).read_text(encoding="utf-8")
                self.assertIn("基线参数", text, name)
                self.assertIn("SP 3.30", text, name)

    def _space(self):
        return build_parameter_space(
            declare_parameter(
                "cash_weight",
                ParameterKind.FACTOR_WEIGHT,
                domain=ParameterDomain.CONTINUOUS,
                minimum=0.0,
                maximum=1.0,
                step=0.05,
                default=0.05,
            )
        )

    def _baseline(self, config):
        return pre_register_baseline(
            space=self._space(),
            parameters={"cash_weight": 0.05},
            metric_name=config.tuning.primary_metric,
            metric=_DEMO_BASELINE_METRIC,
            dataset_fingerprint=_DEMO_DATASET_FINGERPRINT,
            code_version=config.code_version,
        )

    def _selected_trial(self, config, *, cash_weight, metric):
        return ParameterTrial(
            trial_id="demo-trial-1",
            parameters=(Parameter(name="cash_weight", value=cash_weight),),
            dataset_fingerprint=_DEMO_DATASET_FINGERPRINT,
            train_start=config.split.train_start,
            train_end=config.split.train_end,
            validation_start=config.split.validation_start,
            validation_end=config.split.validation_end,
            seed=config.tuning.random_seed,
            code_version=config.code_version,
            metric=metric,
        )


class CoverageThresholdDemonstrationTests(unittest.TestCase):
    """SP 3.10 tie: every example carries the blocking coverage thresholds."""

    def test_every_example_sets_blocking_flags(self) -> None:
        for name in (_HK, _US, _CROSS):
            with self.subTest(name=name):
                coverage = _load(name).coverage
                self.assertTrue(coverage.fx_required, name)
                self.assertTrue(coverage.historical_stock_pool_required, name)
                self.assertTrue(coverage.action_terms_required, name)

    def test_every_example_thresholds_bounded(self) -> None:
        for name in (_HK, _US, _CROSS):
            with self.subTest(name=name):
                coverage = _load(name).coverage
                for pct in (
                    coverage.min_price_coverage_pct,
                    coverage.min_stock_pool_coverage_pct,
                    coverage.min_fundamental_coverage_pct,
                ):
                    self.assertGreaterEqual(pct, 0.0, name)
                    self.assertLessEqual(pct, 100.0, name)


class StressScenarioDemonstrationTests(unittest.TestCase):
    """SP 3.59 tie: the examples' stress scenarios register and gate the conclusion."""

    def test_stress_scenarios_unique_per_example(self) -> None:
        for name in (_HK, _US, _CROSS):
            with self.subTest(name=name):
                config = _load(name)
                names = [scenario.name for scenario in config.stress]
                self.assertEqual(len(names), len(set(names)), name)
                self.assertGreater(len(names), 0, name)

    def test_every_example_scenarios_register(self) -> None:
        for name in (_HK, _US, _CROSS):
            with self.subTest(name=name):
                config = _load(name)
                registry = _registry_for(config)
                required = tuple(
                    RequiredScenario(category=_category_for(s.name), scenario_id=s.name)
                    for s in config.stress
                )
                require_scenarios_registered(
                    registry, required=required, conclusion_label=f"example {name}"
                )
                check = check_scenarios_registered(
                    registry, required=required, conclusion_label=f"example {name}"
                )
                self.assertTrue(check.all_registered, name)

    def test_unregistered_scenario_rejected(self) -> None:
        config = _load(_HK)
        registry = _registry_for(config)
        extra = RequiredScenario(
            category=StressScenarioCategory.CALENDAR, scenario_id="calendar-stress-2023"
        )
        with self.assertRaises(ValueError) as context:
            require_scenarios_registered(
                registry,
                required=(extra,),
                conclusion_label="unregistered example",
            )
        self.assertIn("calendar-stress-2023", str(context.exception))

    def test_cross_market_scenarios_register_cross_market(self) -> None:
        config = _load(_CROSS)
        for registration in _registry_for(config).registrations:
            self.assertIsNone(registration.market, registration.scenario_id)


if __name__ == "__main__":
    unittest.main()
