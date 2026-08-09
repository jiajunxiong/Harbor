"""Rolling validation orchestration tests (MVP 3 / SP 3.34).

Verifies that every fold's validation period produces strategy, benchmark,
risk and data-quality results (在每个折叠的验证期计算策略、基准、风险和数据
质量结果) and that nothing is written back to the training period (不向训练期回
写信息): the SP 3.20 application uses exactly the fold's frozen training fit
and its decision date lies in the fold's validation interval.
"""

import json
import unittest
from dataclasses import replace
from datetime import date

from harbor.core.backtest_config import BenchmarkKind
from harbor.core.backtest_domain import Market
from harbor.core.benchmark import BenchmarkLevel, BenchmarkSeries
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.drawdown_events import DrawdownConfig, DrawdownSeries
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    ParameterSpace,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.performance_metrics import PerformanceMetrics
from harbor.core.rolling_train import run_rolling_training
from harbor.core.rolling_validate import (
    RollingValidationError,
    RollingValidationRun,
    ValidationComponents,
    rolling_validation_fingerprint,
    rolling_validation_json,
    run_rolling_validation,
)
from harbor.core.rolling_window import build_walk_forward_folds
from harbor.core.training_fit import build_training_fit
from harbor.core.trial_budget import TrialBudget
from harbor.core.validation_apply import (
    AppliedStandardization,
    ValidationApplication,
    apply_fingerprint,
)
from harbor.core.validation_config import (
    MetricDirection,
    RetrainFrequency,
    RollingWindowConfig,
    RollingWindowMode,
    TuningConfig,
)
from harbor.core.validation_domain import EvaluationSplit, ManifestComponent

_FINGERPRINT = "f" * 64


def _split(**overrides: object) -> EvaluationSplit:
    """Return a tight pre-registered split with overridable fields."""
    fields: dict[str, object] = {
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 1),
        "validation_end": date(2022, 12, 31),
        "test_start": date(2023, 1, 1),
        "test_end": date(2025, 12, 31),
    }
    fields.update(overrides)
    return EvaluationSplit(**fields)  # type: ignore[arg-type]


def _rolling(**overrides: object) -> RollingWindowConfig:
    """Return an expanding, every-fold rolling config with overridable fields."""
    fields: dict[str, object] = {
        "mode": RollingWindowMode.EXPANDING,
        "train_length_days": None,
        "step_days": 365,
        "retrain_frequency": RetrainFrequency.EVERY_FOLD,
    }
    fields.update(overrides)
    return RollingWindowConfig(**fields)  # type: ignore[arg-type]


def _sequence(**overrides: object):
    """Build the SP 3.31 fold sequence (defaults to 4 folds)."""
    fields: dict[str, object] = {
        "split": _split(),
        "rolling": _rolling(),
        "dataset_fingerprint": _FINGERPRINT,
    }
    fields.update(overrides)
    return build_walk_forward_folds(**fields)  # type: ignore[arg-type]


def _space() -> ParameterSpace:
    """Return a three-parameter space (two weights + one window)."""
    return build_parameter_space(
        declare_parameter(
            name="cash_weight",
            kind=ParameterKind.FACTOR_WEIGHT,
            domain=ParameterDomain.CONTINUOUS,
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            default=0.05,
            markets=(Market.HK, Market.US),
        ),
        declare_parameter(
            name="factor_weight",
            kind=ParameterKind.FACTOR_WEIGHT,
            domain=ParameterDomain.CONTINUOUS,
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            default=0.95,
            markets=(Market.HK, Market.US),
        ),
        declare_parameter(
            name="lookback",
            kind=ParameterKind.WINDOW,
            domain=ParameterDomain.INTEGER,
            minimum=60,
            maximum=504,
            step=24,
            default=252,
            markets=(Market.HK, Market.US),
        ),
    )


def _budget() -> TrialBudget:
    return TrialBudget(max_trials=3, random_seed=42)


def _tuning() -> TuningConfig:
    return TuningConfig(
        primary_metric="sharpe",
        metric_direction=MetricDirection.HIGHER_BETTER,
        max_trials=3,
        random_seed=42,
        min_validation_days=63,
    )


def _candidates() -> list[dict[str, object]]:
    return [
        {"cash_weight": 0.05, "factor_weight": 0.95, "lookback": 252},
        {"cash_weight": 0.10, "factor_weight": 0.90, "lookback": 252},
        {"cash_weight": 0.05, "factor_weight": 0.95, "lookback": 324},
    ]


def _fit_factory(train_start: date, train_end: date):
    """Fit a snapshot confined to the requested training interval."""
    return build_training_fit(
        fit_start=train_start,
        fit_end=train_end,
        dataset_fingerprint=_FINGERPRINT,
        code_version="1.0.0",
        fitted_state=(("lookback", 252.0),),
    )


def _evaluate(fold, parameters: dict[str, object]) -> float:
    """Deterministic validation metric: larger lookback scores higher."""
    return int(parameters["lookback"]) / 1000.0


def _training_run(**overrides: object):
    """Build the SP 3.33 rolling training run with overridable arguments."""
    fields: dict[str, object] = {
        "sequence": _sequence(),
        "space": _space(),
        "budget": _budget(),
        "market": Market.HK,
        "dataset_fingerprint": _FINGERPRINT,
        "code_version": "1.0.0",
        "tuning": _tuning(),
        "candidate_parameter_sets": _candidates(),
        "fit_factory": _fit_factory,
        "evaluate": _evaluate,
        "constraints": (),
        "validation_samples": 200,
    }
    fields.update(overrides)
    return run_rolling_training(**fields)  # type: ignore[arg-type]


def _applied_standardization(decision_date: date) -> AppliedStandardization:
    """Return a minimal applied standardization record."""
    return AppliedStandardization(
        decision_date=decision_date,
        scores=(("AAA", 0.5), ("BBB", -0.5)),
        method=StandardizationMethod.ZSCORE,
    )


def _application(fold_result, decision_date: date) -> ValidationApplication:
    """Build a validation application from the fold's frozen fit."""
    application = ValidationApplication(
        fit_fingerprint=fold_result.fit.fingerprint,
        decision_date=decision_date,
        dataset_fingerprint=fold_result.fit.dataset_fingerprint,
        code_version=fold_result.fit.code_version,
        fingerprint="unfingerprinted",
        standardization=_applied_standardization(decision_date),
    )
    return replace(application, fingerprint=apply_fingerprint(application))


def _compute_validation(fold_result, application) -> ValidationComponents:
    """Compute the four validation results for a fold (deterministic stub)."""
    fold = fold_result.fold
    strategy = PerformanceMetrics(
        start_date=fold.validation_start,
        end_date=fold.validation_end,
        periods=63,
        cumulative_return=0.05,
        annualized_return=0.20,
        annualized_volatility=0.15,
        max_drawdown=-0.05,
        sharpe_ratio=1.2,
        calmar_ratio=1.0,
        downside_deviation=0.08,
    )
    benchmark = BenchmarkSeries(
        kind=BenchmarkKind.CASH,
        levels=(
            BenchmarkLevel(as_of=fold.validation_start, level=1.0, kind=BenchmarkKind.CASH),
            BenchmarkLevel(as_of=fold.validation_end, level=1.02, kind=BenchmarkKind.CASH),
        ),
    )
    risk = DrawdownSeries(config=DrawdownConfig(), events=())
    data_quality = MarketCoverage(
        market=Market.HK,
        scores=(
            CoverageScore(
                market=Market.HK,
                item=ManifestComponent.PRICES,
                measurement=CoverageMeasurement(covered=63, denominator=63),
            ),
        ),
    )
    return ValidationComponents(
        strategy=strategy,
        benchmark=benchmark,
        risk=risk,
        data_quality=data_quality,
    )


def _run(**overrides: object) -> RollingValidationRun:
    """Run the rolling validation orchestration with overridable arguments."""
    fields: dict[str, object] = {
        "training_run": _training_run(),
        "application_factory": _application,
        "compute_validation": _compute_validation,
    }
    fields.update(overrides)
    return run_rolling_validation(**fields)  # type: ignore[arg-type]


class RollingValidationErrorTests(unittest.TestCase):
    """The dedicated error type."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(RollingValidationError, ValueError))


class ValidationIsolationTests(unittest.TestCase):
    """No write-back to training: the frozen fit is applied on validation."""

    def test_application_uses_the_fold_frozen_fit(self) -> None:
        run = _run()
        for result in run:
            self.assertEqual(
                result.application.fit_fingerprint,
                result.training.fit.fingerprint,
            )

    def test_application_decision_date_in_validation(self) -> None:
        run = _run()
        for result in run:
            self.assertEqual(result.application.decision_date, result.fold.validation_end)
            self.assertLessEqual(
                result.fold.validation_start,
                result.application.decision_date,
            )
            self.assertGreaterEqual(
                result.fold.validation_end,
                result.application.decision_date,
            )

    def test_application_inherits_dataset_fingerprint(self) -> None:
        run = _run()
        for result in run:
            self.assertEqual(
                result.application.dataset_fingerprint,
                result.training.fit.dataset_fingerprint,
            )
            self.assertEqual(
                result.application.dataset_fingerprint,
                result.fold.dataset_fingerprint,
            )

    def test_different_fit_fingerprint_rejected(self) -> None:
        run = _run()
        result = run[0]
        bad_application = replace(
            result.application,
            fit_fingerprint="g" * 64,
            fingerprint="abc",
        )
        with self.assertRaises(RollingValidationError):
            replace(result, application=bad_application)

    def test_decision_date_in_training_rejected(self) -> None:
        run = _run()
        result = run[0]
        bad_application = replace(
            result.application,
            decision_date=result.fold.train_end,  # in the training period
            fingerprint="abc",
        )
        with self.assertRaises(RollingValidationError):
            replace(result, application=bad_application)

    def test_decision_date_in_test_rejected(self) -> None:
        run = _run()
        result = run[0]
        bad_application = replace(
            result.application,
            decision_date=result.fold.test_start,  # in the test period
            fingerprint="abc",
        )
        with self.assertRaises(RollingValidationError):
            replace(result, application=bad_application)


class FourResultKindsTests(unittest.TestCase):
    """Every fold computes strategy, benchmark, risk and data-quality results."""

    def test_every_fold_carries_all_four_kinds(self) -> None:
        run = _run()
        for result in run:
            self.assertIsNotNone(result.strategy)
            self.assertIsNotNone(result.benchmark)
            self.assertIsNotNone(result.risk)
            self.assertIsNotNone(result.data_quality)

    def test_strategy_result_recorded(self) -> None:
        result = _run()[0]
        self.assertEqual(result.strategy.cumulative_return, 0.05)
        self.assertEqual(result.strategy.sharpe_ratio, 1.2)
        self.assertEqual(result.strategy.start_date, result.fold.validation_start)

    def test_benchmark_result_recorded(self) -> None:
        result = _run()[0]
        self.assertEqual(result.benchmark.kind, BenchmarkKind.CASH)
        self.assertAlmostEqual(result.benchmark.total_return(), 0.02)

    def test_risk_result_recorded(self) -> None:
        result = _run()[0]
        self.assertEqual(result.risk.config.thresholds, (0.05, 0.08, 0.10))

    def test_data_quality_result_recorded(self) -> None:
        result = _run()[0]
        self.assertEqual(result.data_quality.overall_pct, 100.0)
        self.assertEqual(len(result.data_quality.gaps()), 0)

    def test_one_result_per_fold(self) -> None:
        run = _run()
        self.assertEqual(len(run), 4)
        for index, result in enumerate(run):
            self.assertEqual(result.fold.fold_index, index)


class OrchestrationTests(unittest.TestCase):
    """The run wires the per-fold validation into an auditable value."""

    def test_run_inherits_frozen_context(self) -> None:
        run = _run()
        self.assertEqual(run.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(run.code_version, "1.0.0")

    def test_validation_for_lookup(self) -> None:
        run = _run()
        self.assertIsNotNone(run.validation_for(0))
        self.assertIsNotNone(run.validation_for(3))
        self.assertIsNone(run.validation_for(99))

    def test_fold_property_matches_training(self) -> None:
        run = _run()
        for result in run:
            self.assertIs(result.fold, result.training.fold)

    def test_readable(self) -> None:
        run = _run()
        self.assertIn("4 folds validated", run.readable())
        result = run[0]
        self.assertIn("fold 0", result.readable())
        self.assertIn("strategy", result.readable())
        self.assertIn("benchmark", result.readable())
        self.assertIn("coverage", result.readable())


class FoldValidationResultValidationTests(unittest.TestCase):
    """The per-fold value rejects an inconsistent, leaking record."""

    def test_dataset_fingerprint_mismatch_rejected(self) -> None:
        run = _run()
        result = run[0]
        bad_application = replace(
            result.application,
            dataset_fingerprint="g" * 64,
            fingerprint="abc",
        )
        with self.assertRaises(RollingValidationError):
            replace(result, application=bad_application)


class RollingValidationRunValidationTests(unittest.TestCase):
    """The run value rejects an inconsistent, un-auditable record."""

    def test_empty_results_rejected(self) -> None:
        run = _run()
        with self.assertRaises(RollingValidationError):
            replace(run, results=())

    def test_non_sequential_fold_indices_rejected(self) -> None:
        run = _run()
        second = replace(
            run[1],
            training=replace(run[1].training, fold=replace(run[1].fold, fold_index=2)),
        )
        with self.assertRaises(RollingValidationError):
            replace(run, results=(run[0], second))

    def test_empty_fingerprint_rejected(self) -> None:
        run = _run()
        with self.assertRaises(RollingValidationError):
            replace(run, fingerprint="")

    def test_len_iter_getitem(self) -> None:
        run = _run()
        self.assertEqual(len(run), len(list(run)))
        self.assertEqual(list(run)[2].fold.fold_index, run[2].fold.fold_index)
        with self.assertRaises(IndexError):
            run[99]


class FingerprintTests(unittest.TestCase):
    """The run fingerprint is stable and re-derivable."""

    def test_fingerprint_is_sha256_hex(self) -> None:
        self.assertRegex(_run().fingerprint, r"^[0-9a-f]{64}$")

    def test_fingerprint_rederivable(self) -> None:
        run = _run()
        self.assertEqual(run.fingerprint, rolling_validation_fingerprint(run))

    def test_fingerprint_stable_across_equal_runs(self) -> None:
        self.assertEqual(_run().fingerprint, _run().fingerprint)

    def test_fingerprint_changes_with_strategy(self) -> None:
        def alt_compute(fold_result, application):
            components = _compute_validation(fold_result, application)
            return replace(
                components,
                strategy=replace(components.strategy, cumulative_return=0.99),
            )

        self.assertNotEqual(
            _run(compute_validation=alt_compute).fingerprint,
            _run().fingerprint,
        )

    def test_fingerprint_changes_with_application(self) -> None:
        def alt_application(fold_result, decision_date):
            application = _application(fold_result, decision_date)
            standardization = application.standardization
            assert standardization is not None
            replaced = replace(
                application,
                standardization=replace(
                    standardization,
                    scores=(("AAA", 0.9), ("BBB", -0.9)),
                ),
            )
            return replace(replaced, fingerprint=apply_fingerprint(replaced))

        self.assertNotEqual(
            _run(application_factory=alt_application).fingerprint,
            _run().fingerprint,
        )

    def test_fingerprint_changes_with_fit(self) -> None:
        def alt_fit_factory(train_start: date, train_end: date):
            return build_training_fit(
                fit_start=train_start,
                fit_end=train_end,
                dataset_fingerprint=_FINGERPRINT,
                code_version="1.0.0",
                fitted_state=(("lookback", 324.0),),
            )

        self.assertNotEqual(
            _run(training_run=_training_run(fit_factory=alt_fit_factory)).fingerprint,
            _run().fingerprint,
        )

    def test_json_excludes_derived_fingerprint(self) -> None:
        payload = json.loads(rolling_validation_json(_run()))
        self.assertNotIn("fingerprint", payload)
        self.assertIn("dataset_fingerprint", payload)
        self.assertIn("results", payload)
        self.assertEqual(len(payload["results"]), 4)

    def test_json_is_key_sorted(self) -> None:
        payload = json.loads(rolling_validation_json(_run()))
        self.assertEqual(
            list(payload.keys()),
            ["code_version", "dataset_fingerprint", "results"],
        )
        first = payload["results"][0]
        self.assertEqual(
            list(first.keys()),
            [
                "application_fingerprint",
                "benchmark",
                "data_quality",
                "fold_index",
                "risk",
                "strategy",
            ],
        )
        self.assertEqual(first["fold_index"], 0)
        self.assertIn("cumulative_return", first["strategy"])
        self.assertIn("total_return", first["benchmark"])
        self.assertIn("event_count", first["risk"])
        self.assertIn("overall_pct", first["data_quality"])


if __name__ == "__main__":
    unittest.main()
