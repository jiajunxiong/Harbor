"""Validation-config model tests (MVP 3 / SP 3.2).

Verifies the frozen Pydantic configuration for out-of-sample validation:
the train / validation / test split (SP 3.4), the rolling-window mode and
retrain frequency (SP 3.31), the parameter-search budget and metric
(SP 3.15-3.17), the coverage thresholds (SP 3.10), the stress scenarios
(SP 3.51-3.57) and the conclusion rules (SP 3.58). The config is immutable and
produces a stable key-sorted canonical JSON for SP 3.3 hashing.
"""

import unittest
from datetime import date

from pydantic import ValidationError

from harbor.core.backtest_domain import Currency, Market
from harbor.core.validation_config import (
    ConclusionRulesConfig,
    CoverageThresholdConfig,
    MetricDirection,
    RetrainFrequency,
    RollingWindowConfig,
    RollingWindowMode,
    SplitConfig,
    StressScenario,
    TuningConfig,
    ValidationConfig,
)
from harbor.core.validation_domain import EvaluationSplit


def _split(**overrides: object) -> SplitConfig:
    """Return a valid split config with overridable boundaries."""
    fields: dict[str, object] = {
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 3),
        "validation_end": date(2022, 12, 30),
        "test_start": date(2023, 1, 2),
        "test_end": date(2024, 12, 31),
    }
    fields.update(overrides)
    return SplitConfig(**fields)  # type: ignore[arg-type]


def _config(**overrides: object) -> ValidationConfig:
    """Return a valid validation config with overridable fields."""
    fields: dict[str, object] = {
        "markets": (Market.HK, Market.US),
        "base_currency": Currency.HKD,
        "split": _split(),
    }
    fields.update(overrides)
    return ValidationConfig(**fields)  # type: ignore[arg-type]


class SplitConfigTests(unittest.TestCase):
    """Verify the train / validation / test boundary model (SP 3.4)."""

    def test_valid_split_and_value_conversion(self) -> None:
        split = _split()
        value = split.to_evaluation_split()
        self.assertIsInstance(value, EvaluationSplit)
        self.assertEqual(value.test_start, date(2023, 1, 2))

    def test_rejects_overlapping_boundaries(self) -> None:
        # training ends on the same day validation starts -> must be rejected.
        with self.assertRaises(ValidationError):
            _split(
                train_end=date(2022, 1, 3),
                validation_start=date(2022, 1, 3),
            )

    def test_rejects_reversed_range(self) -> None:
        with self.assertRaises(ValidationError):
            _split(train_start=date(2022, 1, 1), train_end=date(2019, 1, 1))

    def test_rejects_test_overlapping_validation(self) -> None:
        with self.assertRaises(ValidationError):
            _split(
                validation_end=date(2023, 1, 2),
                test_start=date(2023, 1, 2),
            )

    def test_single_day_validation_is_allowed(self) -> None:
        split = _split(
            validation_start=date(2022, 1, 3),
            validation_end=date(2022, 1, 3),
        )
        self.assertEqual(split.to_evaluation_split().validation_days, 1)


class RollingWindowConfigTests(unittest.TestCase):
    """Verify the rolling-window and retrain model (SP 3.31)."""

    def test_expanding_default(self) -> None:
        rolling = RollingWindowConfig()
        self.assertIs(rolling.mode, RollingWindowMode.EXPANDING)
        self.assertIsNone(rolling.train_length_days)

    def test_fixed_requires_train_length(self) -> None:
        with self.assertRaises(ValidationError):
            RollingWindowConfig(mode=RollingWindowMode.FIXED)

    def test_expanding_rejects_train_length(self) -> None:
        with self.assertRaises(ValidationError):
            RollingWindowConfig(mode=RollingWindowMode.EXPANDING, train_length_days=504)

    def test_fixed_with_train_length(self) -> None:
        rolling = RollingWindowConfig(
            mode=RollingWindowMode.FIXED,
            train_length_days=504,
            retrain_frequency=RetrainFrequency.ANNUAL,
        )
        self.assertIs(rolling.retrain_frequency, RetrainFrequency.ANNUAL)

    def test_step_days_must_be_positive(self) -> None:
        with self.assertRaises(ValidationError):
            RollingWindowConfig(step_days=0)


class TuningConfigTests(unittest.TestCase):
    """Verify the parameter-search budget model (SP 3.15-3.17)."""

    def test_defaults(self) -> None:
        tuning = TuningConfig()
        self.assertEqual(tuning.primary_metric, "sharpe")
        self.assertIs(tuning.metric_direction, MetricDirection.HIGHER_BETTER)
        self.assertEqual(tuning.random_seed, 42)

    def test_requires_metric_name(self) -> None:
        with self.assertRaises(ValidationError):
            TuningConfig(primary_metric=" ")

    def test_trials_must_be_positive(self) -> None:
        with self.assertRaises(ValidationError):
            TuningConfig(max_trials=0)


class CoverageThresholdConfigTests(unittest.TestCase):
    """Verify the coverage threshold model (SP 3.10)."""

    def test_defaults(self) -> None:
        coverage = CoverageThresholdConfig()
        self.assertTrue(coverage.fx_required)
        self.assertTrue(coverage.historical_stock_pool_required)
        self.assertTrue(coverage.action_terms_required)

    def test_percentages_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            CoverageThresholdConfig(min_price_coverage_pct=101.0)
        with self.assertRaises(ValidationError):
            CoverageThresholdConfig(min_stock_pool_coverage_pct=-1.0)


class StressScenarioTests(unittest.TestCase):
    """Verify the stress-scenario model (SP 3.51-3.57)."""

    def test_valid_scenario(self) -> None:
        scenario = StressScenario(name="covid-shock", fx_shift_bps=-200.0)
        self.assertEqual(scenario.cost_multiplier, 2.0)
        self.assertEqual(scenario.fx_shift_bps, -200.0)

    def test_requires_name(self) -> None:
        with self.assertRaises(ValidationError):
            StressScenario(name=" ")

    def test_cost_multiplier_must_be_at_least_one(self) -> None:
        with self.assertRaises(ValidationError):
            StressScenario(name="low-cost", cost_multiplier=0.5)


class ValidationConfigTests(unittest.TestCase):
    """Verify the top-level validation config (SP 3.2)."""

    def test_valid_config(self) -> None:
        config = _config()
        self.assertEqual(config.base_currency, Currency.HKD)
        self.assertIsInstance(config.split.to_evaluation_split(), EvaluationSplit)
        self.assertEqual(config.rolling.mode, RollingWindowMode.EXPANDING)

    def test_requires_a_market(self) -> None:
        with self.assertRaises(ValidationError):
            _config(markets=())

    def test_rejects_duplicate_markets(self) -> None:
        with self.assertRaises(ValidationError):
            _config(markets=(Market.HK, Market.HK))

    def test_stress_names_must_be_unique(self) -> None:
        scenario = StressScenario(name="shock")
        with self.assertRaises(ValidationError):
            _config(stress=(scenario, scenario))

    def test_split_must_end_before_cutoff(self) -> None:
        with self.assertRaises(ValidationError):
            _config(data_cutoff=date(2023, 12, 31))

    def test_is_frozen(self) -> None:
        config = _config()
        with self.assertRaises(ValidationError):
            config.code_version = "2.0.0"  # type: ignore[misc]
        with self.assertRaises(ValidationError):
            config.tuning.max_trials = 5  # type: ignore[misc]

    def test_canonical_json_is_stable_and_key_sorted(self) -> None:
        first = _config()
        second = _config()
        self.assertEqual(first.canonical_json(), second.canonical_json())
        payload = first.canonical_json()
        # Dates and enums serialize to stable scalar values.
        self.assertIn('"train_start":"2019-01-01"', payload)
        self.assertIn('"markets":["HK","US"]', payload)
        # Key-sorted: base_currency before code_version.
        self.assertLess(payload.index('"base_currency"'), payload.index('"code_version"'))

    def test_canonical_json_changes_with_boundaries(self) -> None:
        base = _config()
        other = _config(split=_split(test_end=date(2024, 6, 30)))
        self.assertNotEqual(base.canonical_json(), other.canonical_json())

    def test_conclusion_rules_are_configurable(self) -> None:
        config = _config(
            conclusion=ConclusionRulesConfig(
                min_qualified_fold_ratio=0.9,
                max_allowed_drawdown_pct=25.0,
            )
        )
        self.assertEqual(config.conclusion.min_qualified_fold_ratio, 0.9)


if __name__ == "__main__":
    unittest.main()
