"""Parameter-neighborhood sensitivity tests (MVP 3 / SP 3.57).

Verifies that a finite grid over a pre-registered neighborhood around the
selected parameters is run and its plateaus (台面), cliffs (悬崖) and
infeasible regions (不可行区域) are output — without re-selecting parameters
(不进行二次选参).
"""

import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

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
from harbor.core.holdout_registry import register_test_set
from harbor.core.parameter_constraints import (
    ConstraintKind,
    ParameterConstraint,
    constraint,
)
from harbor.core.parameter_neighborhood import (
    NeighborhoodClassification,
    NeighborhoodPoint,
    NeighborhoodSensitivityReport,
    ParameterNeighborhoodError,
    build_neighborhood_config,
    compute_parameter_neighborhood,
    default_neighborhood_config,
    neighborhood_config_fingerprint,
    neighborhood_fingerprint,
    neighborhood_json,
    no_reselection_statement,
)
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    ParameterSpace,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.performance_metrics import PerformanceMetrics
from harbor.core.replay_manifest import DataQueryBoundaries, ReplayManifest
from harbor.core.rolling_oos import OosRunOutcome, run_rolling_oos
from harbor.core.rolling_train import run_rolling_training
from harbor.core.rolling_validate import (
    ValidationComponents,
    run_rolling_validation,
)
from harbor.core.rolling_window import build_walk_forward_folds
from harbor.core.test_access_guard import AccessGuard
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
from harbor.core.validation_domain import (
    EvaluationSplit,
    ManifestComponent,
    Parameter,
    ParameterTrial,
    ValidationStatus,
)

_FINGERPRINT = "f" * 64
_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_OOS_START = date(2023, 1, 1)
_OOS_END = date(2026, 12, 30)


def _space() -> ParameterSpace:
    """The three-parameter stepped space (two weights + one window)."""
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


def _trial(**overrides: object) -> ParameterTrial:
    """A selected parameter trial with the reference metric."""
    fields: dict[str, object] = {
        "trial_id": "trial-1",
        "parameters": (
            Parameter(name="cash_weight", value=0.05),
            Parameter(name="factor_weight", value=0.95),
            Parameter(name="lookback", value=252),
        ),
        "dataset_fingerprint": _FINGERPRINT,
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 1),
        "validation_end": date(2022, 12, 31),
        "seed": 42,
        "code_version": "1.0.0",
        "metric": 0.252,
    }
    fields.update(overrides)
    return ParameterTrial(**fields)  # type: ignore[arg-type]


def _evaluate(parameters: dict[str, object]) -> float:
    """Deterministic metric: cliffs below 228, plateau around 252, else up."""
    lookback = int(parameters["lookback"])
    if lookback <= 228:
        return 0.18
    if 240 <= lookback <= 264:
        return 0.252
    return 0.30


def _weight_sum_constraint() -> ParameterConstraint:
    """The cash + factor weights must sum to one."""
    return constraint(
        "weight-sum",
        ConstraintKind.SUM_TO_TARGET,
        "cash_weight",
        "factor_weight",
        target=1.0,
    )


def _report(**overrides: object) -> NeighborhoodSensitivityReport:
    """Compute the default neighborhood with overridable arguments."""
    fields: dict[str, object] = {
        "selected": _trial(),
        "config": default_neighborhood_config(),
        "space": _space(),
        "market": Market.US,
        "evaluate": _evaluate,
        "constraints": (),
    }
    fields.update(overrides)
    return compute_parameter_neighborhood(**fields)  # type: ignore[arg-type]


def _point(**overrides: object) -> NeighborhoodPoint:
    """A minimal plateau point for value-level tests."""
    fields: dict[str, object] = {
        "parameter_name": "lookback",
        "offset_steps": 1,
        "parameters": (Parameter(name="lookback", value=276),),
        "feasible": True,
        "infeasible_reason": None,
        "metric": 0.30,
        "classification": NeighborhoodClassification.IMPROVEMENT,
    }
    fields.update(overrides)
    return NeighborhoodPoint(**fields)  # type: ignore[arg-type]


def _split(**overrides: object) -> EvaluationSplit:
    """Return a tight pre-registered split (final fold is full-length)."""
    fields: dict[str, object] = {
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 1),
        "validation_end": date(2022, 12, 31),
        "test_start": _OOS_START,
        "test_end": _OOS_END,
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


def _evaluate_stub(fold, parameters: dict[str, object]) -> float:
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
        "evaluate": _evaluate_stub,
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


def _validation_run(**overrides: object):
    """Build the SP 3.34 rolling validation run with overridable arguments."""
    fields: dict[str, object] = {
        "training_run": _training_run(),
        "application_factory": _application,
        "compute_validation": _compute_validation,
    }
    fields.update(overrides)
    return run_rolling_validation(**fields)  # type: ignore[arg-type]


def _registration(**overrides: object):
    """Register the independent holdout over the base split."""
    fields: dict[str, object] = {
        "test_set_id": "holdout-1",
        "split": _split(),
        "config_hash": "cfg-hash",
    }
    fields.update(overrides)
    return register_test_set(**fields)  # type: ignore[arg-type]


def _guard(**overrides: object) -> AccessGuard:
    """Return an access guard over the registered holdout."""
    fields: dict[str, object] = {"registration": _registration()}
    fields.update(overrides)
    return AccessGuard(**fields)  # type: ignore[arg-type]


def _manifest(fold, run_id: str) -> ReplayManifest:
    """Build a replay manifest covering the fold's OOS segment."""
    return ReplayManifest(
        run_id=run_id,
        config_hash="cfg-hash",
        code_version="1.0.0",
        data_boundaries=DataQueryBoundaries(
            start_date=fold.test_start,
            end_date=fold.test_end,
            data_cutoff=fold.test_end,
        ),
        fx_source="fx-1",
        calendar_version="cal-1",
        random_seed=42,
    )


def _run_engine(fold, selected) -> OosRunOutcome:
    """Deterministic MVP 2 engine stub for one fold's OOS segment."""
    run_id = f"oos-run-{fold.fold_index}"
    return OosRunOutcome(run_id=run_id, replay_manifest=_manifest(fold, run_id))


def _oos_run(**overrides: object):
    """Run the SP 3.35 rolling OOS execution with overridable arguments."""
    fields: dict[str, object] = {
        "validation_run": _validation_run(),
        "guard": _guard(),
        "current_stage": ValidationStatus.TEST_LOCKED,
        "run_engine": _run_engine,
        "requested_at": _AT,
    }
    fields.update(overrides)
    return run_rolling_oos(**fields)  # type: ignore[arg-type]


class ParameterNeighborhoodErrorTests(unittest.TestCase):
    """The dedicated error type."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(ParameterNeighborhoodError, ValueError))

    def test_cliff_at_or_below_plateau_rejected(self) -> None:
        with self.assertRaises(ParameterNeighborhoodError):
            build_neighborhood_config(
                version="v1",
                steps=2,
                plateau_tolerance=0.05,
                cliff_threshold=0.05,
            )


class NeighborhoodClassificationTests(unittest.TestCase):
    """The five classifications."""

    def test_five_classifications(self) -> None:
        self.assertEqual(
            tuple(NeighborhoodClassification),
            (
                NeighborhoodClassification.INFEASIBLE,
                NeighborhoodClassification.PLATEAU,
                NeighborhoodClassification.CLIFF,
                NeighborhoodClassification.IMPROVEMENT,
                NeighborhoodClassification.NEUTRAL,
            ),
        )


class ParameterNeighborhoodConfigTests(unittest.TestCase):
    """The pre-registered neighborhood."""

    def test_default_config(self) -> None:
        config = default_neighborhood_config()
        self.assertEqual(config.version, "neighborhood-default")
        self.assertEqual(config.steps, 2)
        self.assertEqual(config.plateau_tolerance, 0.01)
        self.assertEqual(config.cliff_threshold, 0.05)

    def test_fingerprint_rederivable_stable(self) -> None:
        config = default_neighborhood_config()
        self.assertEqual(config.fingerprint, neighborhood_config_fingerprint(config))
        self.assertEqual(config.fingerprint, default_neighborhood_config().fingerprint)

    def test_zero_steps_rejected(self) -> None:
        with self.assertRaises(ParameterNeighborhoodError):
            build_neighborhood_config(
                version="v1", steps=0, plateau_tolerance=0.01, cliff_threshold=0.05
            )

    def test_negative_plateau_rejected(self) -> None:
        with self.assertRaises(ParameterNeighborhoodError):
            build_neighborhood_config(
                version="v1", steps=1, plateau_tolerance=-0.1, cliff_threshold=0.05
            )

    def test_readable(self) -> None:
        self.assertIn("neighborhood-default", default_neighborhood_config().readable())


class NeighborhoodPointTests(unittest.TestCase):
    """One grid point."""

    def test_valid_improvement(self) -> None:
        point = _point()
        self.assertTrue(point.feasible)
        self.assertIs(point.classification, NeighborhoodClassification.IMPROVEMENT)

    def test_valid_infeasible(self) -> None:
        point = _point(
            parameters=(),
            feasible=False,
            infeasible_reason="sum must equal 1",
            metric=None,
            classification=NeighborhoodClassification.INFEASIBLE,
        )
        self.assertFalse(point.feasible)

    def test_infeasible_with_metric_rejected(self) -> None:
        with self.assertRaises(ParameterNeighborhoodError):
            _point(
                parameters=(),
                feasible=False,
                infeasible_reason="boom",
                metric=0.1,
                classification=NeighborhoodClassification.INFEASIBLE,
            )

    def test_infeasible_without_reason_rejected(self) -> None:
        with self.assertRaises(ParameterNeighborhoodError):
            _point(
                parameters=(),
                feasible=False,
                infeasible_reason=None,
                metric=None,
                classification=NeighborhoodClassification.INFEASIBLE,
            )

    def test_feasible_without_metric_rejected(self) -> None:
        with self.assertRaises(ParameterNeighborhoodError):
            _point(metric=None)

    def test_readable(self) -> None:
        self.assertIn("lookback +1", _point().readable())


class NeighborhoodSensitivityReportTests(unittest.TestCase):
    """The finite-grid neighborhood report."""

    def test_valid_report(self) -> None:
        report = _report()
        self.assertEqual(report.point_count, 10)
        self.assertEqual(report.plateau_count, 6)
        self.assertEqual(report.cliff_count, 2)
        self.assertEqual(report.infeasible_count, 0)
        self.assertEqual(report.improvement_count, 2)
        self.assertEqual(report.neutral_count, 0)

    def test_empty_points_rejected(self) -> None:
        report = _report()
        with self.assertRaises(ParameterNeighborhoodError):
            NeighborhoodSensitivityReport(
                config=report.config,
                trial_id=report.trial_id,
                selected_metric=report.selected_metric,
                selected_parameters=report.selected_parameters,
                points=(),
                dataset_fingerprint=report.dataset_fingerprint,
                code_version=report.code_version,
                plateau_count=0,
                cliff_count=0,
                infeasible_count=0,
                improvement_count=0,
                neutral_count=0,
                fingerprint="x" * 64,
            )

    def test_count_inconsistent_rejected(self) -> None:
        report = _report()
        with self.assertRaises(ParameterNeighborhoodError):
            NeighborhoodSensitivityReport(
                config=report.config,
                trial_id=report.trial_id,
                selected_metric=report.selected_metric,
                selected_parameters=report.selected_parameters,
                points=report.points,
                dataset_fingerprint=report.dataset_fingerprint,
                code_version=report.code_version,
                plateau_count=99,
                cliff_count=report.cliff_count,
                infeasible_count=report.infeasible_count,
                improvement_count=report.improvement_count,
                neutral_count=report.neutral_count,
                fingerprint="x" * 64,
            )

    def test_len_iter_getitem(self) -> None:
        report = _report()
        self.assertEqual(report[0].parameter_name, "cash_weight")
        self.assertEqual(len(list(report)), 10)

    def test_no_reselection_statement(self) -> None:
        self.assertIn("does not re-select", no_reselection_statement())

    def test_readable(self) -> None:
        self.assertIn("no re-selection", _report().readable())


class ComputeParameterNeighborhoodTests(unittest.TestCase):
    """The finite grid around the selected parameters."""

    def test_grid_size(self) -> None:
        report = _report()
        # cash_weight 3 (offset -1,+1,+2; -2 out of bounds), factor_weight 3, lookback 4.
        self.assertEqual(report.point_count, 10)

    def test_plateaus_and_cliffs(self) -> None:
        report = _report()
        self.assertEqual(report.plateau_count, 6)
        self.assertEqual(report.cliff_count, 2)
        self.assertEqual(report.improvement_count, 2)

    def test_lookback_cliff_points(self) -> None:
        report = _report()
        cliffs = [p for p in report.points if p.classification is NeighborhoodClassification.CLIFF]
        self.assertEqual(len(cliffs), 2)
        self.assertTrue(all(p.parameter_name == "lookback" for p in cliffs))
        self.assertEqual(sorted(p.offset_steps for p in cliffs), [-2, -1])
        for point in cliffs:
            self.assertAlmostEqual(point.metric, 0.18, places=6)

    def test_weight_neighbors_are_plateau(self) -> None:
        report = _report()
        for point in report.points:
            if point.parameter_name in ("cash_weight", "factor_weight"):
                self.assertIs(point.classification, NeighborhoodClassification.PLATEAU)

    def test_point_parameters_validated(self) -> None:
        report = _report()
        lookback_up = next(
            p for p in report.points if p.parameter_name == "lookback" and p.offset_steps == 1
        )
        self.assertEqual(
            {parameter.name: parameter.value for parameter in lookback_up.parameters}["lookback"],
            276,
        )

    def test_constraint_creates_infeasible_region(self) -> None:
        report = _report(constraints=(_weight_sum_constraint(),))
        # varying a single weight breaks the sum -> infeasible; lookback stays feasible.
        self.assertEqual(report.infeasible_count, 6)
        self.assertEqual(report.plateau_count, 0)
        self.assertEqual(report.cliff_count, 2)
        self.assertEqual(report.improvement_count, 2)
        for point in report.points:
            if point.parameter_name in ("cash_weight", "factor_weight"):
                self.assertIs(point.classification, NeighborhoodClassification.INFEASIBLE)
                self.assertIn("weight-sum", point.infeasible_reason)

    def test_one_step_grid_smaller(self) -> None:
        config = build_neighborhood_config(
            version="v1", steps=1, plateau_tolerance=0.01, cliff_threshold=0.05
        )
        report = _report(config=config)
        # cash_weight 2, factor_weight 2, lookback 2.
        self.assertEqual(report.point_count, 6)

    def test_report_context(self) -> None:
        report = _report()
        self.assertEqual(report.trial_id, "trial-1")
        self.assertEqual(report.selected_metric, 0.252)
        self.assertEqual(report.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(report.code_version, "1.0.0")

    def test_selected_without_metric_rejected(self) -> None:
        with self.assertRaises(ParameterNeighborhoodError):
            _report(selected=_trial(metric=None, failed_reason="failed"))


class FingerprintTests(unittest.TestCase):
    """Stable, re-derivable, sensitive neighborhood fingerprints."""

    def test_config_sha256_rederivable(self) -> None:
        config = default_neighborhood_config()
        self.assertEqual(len(config.fingerprint), 64)
        int(config.fingerprint, 16)
        self.assertEqual(config.fingerprint, neighborhood_config_fingerprint(config))

    def test_config_changes_with_parameter(self) -> None:
        base = default_neighborhood_config()
        other = build_neighborhood_config(
            version="v1",
            source="pre-registered",
            steps=3,
            plateau_tolerance=base.plateau_tolerance,
            cliff_threshold=base.cliff_threshold,
        )
        self.assertNotEqual(base.fingerprint, other.fingerprint)

    def test_report_sha256_rederivable(self) -> None:
        report = _report()
        self.assertEqual(len(report.fingerprint), 64)
        int(report.fingerprint, 16)
        self.assertEqual(report.fingerprint, neighborhood_fingerprint(report))

    def test_report_stable(self) -> None:
        self.assertEqual(_report().fingerprint, _report().fingerprint)

    def test_report_changes_with_evaluate(self) -> None:
        def flat_evaluate(parameters):
            return 0.252

        self.assertNotEqual(
            _report().fingerprint,
            _report(evaluate=flat_evaluate).fingerprint,
        )

    def test_json_excludes_fingerprint_and_key_sorted(self) -> None:
        report = _report()
        serialized = neighborhood_json(report)
        self.assertNotIn('"fingerprint"', serialized)
        self.assertIn('"version":"neighborhood-default"', serialized)
        self.assertIn('"plateau_count":6', serialized)
        self.assertIn('"cliff_count":2', serialized)
        payload = json.loads(serialized)
        self.assertEqual(
            serialized,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )
