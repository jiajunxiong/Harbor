"""Rolling backtest failure recovery tests (MVP 3 / SP 3.43).

Verifies that a single failed fold preserves its diagnostics and, per
configuration, sets the overall status to failed or not-qualified, and that
omitting failed folds to continue aggregation is forbidden (单一折叠失败保留诊
断并按配置将整体状态置为失败或不合格；禁止省略失败折叠后继续汇总).
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
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    ParameterSpace,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.performance_metrics import PerformanceMetrics
from harbor.core.replay_manifest import DataQueryBoundaries, ReplayManifest
from harbor.core.rolling_failure import (
    FailureSeverity,
    FoldFailureDiagnostics,
    RollingFailureError,
    RollingFailurePolicy,
    RollingFailureRecovery,
    check_rolling_failures,
    require_aggregation_allowed,
    rolling_failure_fingerprint,
    rolling_failure_json,
)
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
    ValidationStatus,
)

_FINGERPRINT = "f" * 64
_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _split(**overrides: object) -> EvaluationSplit:
    """Return a tight pre-registered split (final fold is full-length)."""
    fields: dict[str, object] = {
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 1),
        "validation_end": date(2022, 12, 31),
        "test_start": date(2023, 1, 1),
        "test_end": date(2026, 12, 30),
    }
    fields.update(overrides)
    return EvaluationSplit(**fields)  # type: ignore[arg-type]


def _narrow_split() -> EvaluationSplit:
    """A holdout split that excludes fold 0's OOS interval (test starts 2024)."""
    return _split(test_start=date(2024, 1, 1))


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


def _mixed_oos_run():
    """A run where fold 0 is outside a narrower holdout (fails), others execute."""
    return _oos_run(
        guard=_guard(registration=_registration(split=_narrow_split())),
    )


def _recovery(**overrides: object) -> RollingFailureRecovery:
    """Compute the failure recovery with overridable arguments."""
    fields: dict[str, object] = {
        "oos_run": _mixed_oos_run(),
        "policy": RollingFailurePolicy(),
    }
    fields.update(overrides)
    return check_rolling_failures(**fields)  # type: ignore[arg-type]


def _diagnostics(**overrides: object) -> FoldFailureDiagnostics:
    """A minimal fold diagnostics record for value-type tests."""
    fields: dict[str, object] = {
        "fold_index": 0,
        "failure_reason": "boom",
    }
    fields.update(overrides)
    return FoldFailureDiagnostics(**fields)  # type: ignore[arg-type]


class RollingFailureErrorTests(unittest.TestCase):
    """The dedicated error type."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(RollingFailureError, ValueError))

    def test_negative_fold_index_rejected(self) -> None:
        with self.assertRaises(RollingFailureError):
            _diagnostics(fold_index=-1)

    def test_empty_failure_reason_rejected(self) -> None:
        with self.assertRaises(RollingFailureError):
            _diagnostics(failure_reason="")


class RollingFailurePolicyTests(unittest.TestCase):
    """The failure-severity configuration."""

    def test_default_severity_is_failed(self) -> None:
        self.assertEqual(RollingFailurePolicy().severity, FailureSeverity.FAILED)

    def test_custom_severity(self) -> None:
        policy = RollingFailurePolicy(severity=FailureSeverity.NOT_QUALIFIED)
        self.assertEqual(policy.severity, FailureSeverity.NOT_QUALIFIED)

    def test_readable(self) -> None:
        self.assertIn("failed", RollingFailurePolicy().readable())
        self.assertIn(
            "not_qualified",
            RollingFailurePolicy(severity=FailureSeverity.NOT_QUALIFIED).readable(),
        )

    def test_severity_maps_to_terminal_status(self) -> None:
        recovery_failed = _recovery(policy=RollingFailurePolicy(severity=FailureSeverity.FAILED))
        self.assertEqual(recovery_failed.overall_status, ValidationStatus.FAILED)
        recovery_nq = _recovery(policy=RollingFailurePolicy(severity=FailureSeverity.NOT_QUALIFIED))
        self.assertEqual(recovery_nq.overall_status, ValidationStatus.NOT_QUALIFIED)


class FoldFailureDiagnosticsTests(unittest.TestCase):
    """The preserved per-fold diagnostics value type."""

    def test_valid_record(self) -> None:
        diagnostics = _diagnostics(fold_index=2, failure_reason="no selected candidate")
        self.assertEqual(diagnostics.fold_index, 2)
        self.assertEqual(diagnostics.failure_reason, "no selected candidate")

    def test_negative_index_rejected(self) -> None:
        with self.assertRaises(RollingFailureError):
            _diagnostics(fold_index=-1)

    def test_empty_reason_rejected(self) -> None:
        with self.assertRaises(RollingFailureError):
            _diagnostics(failure_reason="")

    def test_readable(self) -> None:
        self.assertIn("fold 0: boom", _diagnostics().readable())


class CheckRollingFailuresTests(unittest.TestCase):
    """The non-raising failure-recovery orchestration."""

    def test_clean_run_no_failures(self) -> None:
        recovery = check_rolling_failures(_oos_run(), policy=RollingFailurePolicy())
        self.assertEqual(recovery.failed_folds, ())
        self.assertTrue(recovery.aggregation_allowed)
        self.assertIsNone(recovery.overall_status)
        self.assertIsNone(recovery.reason)

    def test_single_failed_fold_diagnostics_preserved(self) -> None:
        recovery = _recovery()
        self.assertEqual(len(recovery.failed_folds), 1)
        diagnostics = recovery.failed_folds[0]
        self.assertEqual(diagnostics.fold_index, 0)
        self.assertIn("outside the registered holdout", diagnostics.failure_reason)

    def test_multiple_failed_folds_ordered(self) -> None:
        recovery = check_rolling_failures(
            _oos_run(current_stage=ValidationStatus.TUNING),
            policy=RollingFailurePolicy(),
        )
        indices = tuple(diagnostics.fold_index for diagnostics in recovery.failed_folds)
        self.assertEqual(indices, (0, 1, 2, 3))
        self.assertIn("not authorized", recovery.failed_folds[0].failure_reason)

    def test_default_policy_sets_failed_status(self) -> None:
        recovery = _recovery()
        self.assertEqual(recovery.overall_status, ValidationStatus.FAILED)
        self.assertFalse(recovery.aggregation_allowed)

    def test_not_qualified_policy_sets_not_qualified(self) -> None:
        recovery = _recovery(policy=RollingFailurePolicy(severity=FailureSeverity.NOT_QUALIFIED))
        self.assertEqual(recovery.overall_status, ValidationStatus.NOT_QUALIFIED)

    def test_aggregation_forbidden_on_failure(self) -> None:
        self.assertFalse(_recovery().aggregation_allowed)

    def test_reason_names_failure_count(self) -> None:
        recovery = _recovery()
        self.assertIsNotNone(recovery.reason)
        assert recovery.reason is not None
        self.assertIn("1 of 4 fold(s) failed", recovery.reason)

    def test_iteration_and_indexing(self) -> None:
        recovery = _recovery()
        self.assertEqual(len(recovery), 1)
        self.assertEqual(recovery[0].fold_index, 0)
        self.assertEqual([d.fold_index for d in recovery], [0])
        self.assertEqual(recovery.failed_count, 1)


class RequireAggregationTests(unittest.TestCase):
    """The no-omit guard — failed folds are never dropped to keep aggregating."""

    def test_clean_run_allowed(self) -> None:
        recovery = check_rolling_failures(_oos_run(), policy=RollingFailurePolicy())
        self.assertIs(require_aggregation_allowed(recovery), recovery)

    def test_failed_folds_raise(self) -> None:
        with self.assertRaises(RollingFailureError):
            require_aggregation_allowed(_recovery())

    def test_error_names_fold_and_reason(self) -> None:
        with self.assertRaises(RollingFailureError) as ctx:
            require_aggregation_allowed(_recovery())
        message = str(ctx.exception)
        self.assertIn("omitting failed folds", message)
        self.assertIn("fold 0", message)
        self.assertIn("outside the registered holdout", message)

    def test_error_forbids_aggregation(self) -> None:
        with self.assertRaises(RollingFailureError) as ctx:
            require_aggregation_allowed(_recovery())
        self.assertIn("aggregation", str(ctx.exception))


class RecoveryValidationTests(unittest.TestCase):
    """The recovery record invariants."""

    def test_failed_folds_must_be_ordered(self) -> None:
        with self.assertRaises(RollingFailureError):
            RollingFailureRecovery(
                failed_folds=(_diagnostics(fold_index=1), _diagnostics(fold_index=0)),
                policy=RollingFailurePolicy(),
                overall_status=ValidationStatus.FAILED,
                aggregation_allowed=False,
                reason="failed",
                fingerprint="x" * 64,
            )

    def test_aggregation_allowed_with_failures_rejected(self) -> None:
        with self.assertRaises(RollingFailureError):
            RollingFailureRecovery(
                failed_folds=(_diagnostics(),),
                policy=RollingFailurePolicy(),
                overall_status=ValidationStatus.FAILED,
                aggregation_allowed=True,
                reason="failed",
                fingerprint="x" * 64,
            )

    def test_overall_status_without_failures_rejected(self) -> None:
        with self.assertRaises(RollingFailureError):
            RollingFailureRecovery(
                failed_folds=(),
                policy=RollingFailurePolicy(),
                overall_status=ValidationStatus.FAILED,
                aggregation_allowed=True,
                reason=None,
                fingerprint="x" * 64,
            )

    def test_overall_status_mismatch_rejected(self) -> None:
        with self.assertRaises(RollingFailureError):
            RollingFailureRecovery(
                failed_folds=(_diagnostics(),),
                policy=RollingFailurePolicy(),
                overall_status=ValidationStatus.NOT_QUALIFIED,
                aggregation_allowed=False,
                reason="failed",
                fingerprint="x" * 64,
            )

    def test_reason_required_when_failed(self) -> None:
        with self.assertRaises(RollingFailureError):
            RollingFailureRecovery(
                failed_folds=(_diagnostics(),),
                policy=RollingFailurePolicy(),
                overall_status=ValidationStatus.FAILED,
                aggregation_allowed=False,
                reason=None,
                fingerprint="x" * 64,
            )

    def test_empty_fingerprint_rejected(self) -> None:
        with self.assertRaises(RollingFailureError):
            RollingFailureRecovery(
                failed_folds=(),
                policy=RollingFailurePolicy(),
                overall_status=None,
                aggregation_allowed=True,
                reason=None,
                fingerprint="",
            )

    def test_readable(self) -> None:
        clean = check_rolling_failures(_oos_run(), policy=RollingFailurePolicy())
        self.assertIn("aggregation allowed", clean.readable())
        failed = _recovery()
        self.assertIn("aggregation forbidden", failed.readable())
        self.assertIn("overall FAILED", failed.readable())


class FingerprintTests(unittest.TestCase):
    """Stable, re-derivable, sensitive recovery fingerprints."""

    def test_sha256_hex(self) -> None:
        digest = _recovery().fingerprint
        self.assertEqual(len(digest), 64)
        int(digest, 16)

    def test_rederivable(self) -> None:
        recovery = _recovery()
        self.assertEqual(recovery.fingerprint, rolling_failure_fingerprint(recovery))

    def test_stable(self) -> None:
        self.assertEqual(_recovery().fingerprint, _recovery().fingerprint)

    def test_changes_with_failure(self) -> None:
        clean = check_rolling_failures(_oos_run(), policy=RollingFailurePolicy())
        failed = _recovery()
        self.assertNotEqual(clean.fingerprint, failed.fingerprint)

    def test_changes_with_policy_severity(self) -> None:
        failed = _recovery()
        not_qualified = _recovery(
            policy=RollingFailurePolicy(severity=FailureSeverity.NOT_QUALIFIED)
        )
        self.assertNotEqual(failed.fingerprint, not_qualified.fingerprint)

    def test_json_excludes_fingerprint_and_key_sorted(self) -> None:
        recovery = _recovery()
        serialized = rolling_failure_json(recovery)
        self.assertNotIn('"fingerprint"', serialized)
        self.assertIn('"overall_status":"FAILED"', serialized)
        self.assertIn('"aggregation_allowed":false', serialized)
        payload = json.loads(serialized)
        self.assertEqual(
            serialized,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )
