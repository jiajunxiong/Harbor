"""Final holdout execution tests (MVP 3 / SP 3.41).

Verifies the independent holdout is unlocked exactly once and only after the
training/validation selection is frozen (TEST_LOCKED), and that the unlock
event, the responsibility statement and the input fingerprint are all saved
(仅在训练/验证选择冻结后解锁一次独立保留集；解锁事件、责任说明和输入指纹必须
保存).
"""

import hashlib
import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

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
from harbor.core.final_holdout import (
    FinalHoldoutError,
    FinalHoldoutInputs,
    FinalHoldoutRelease,
    FinalHoldoutUnlockError,
    HoldoutUnlockEvent,
    final_holdout_fingerprint,
    final_holdout_input_fingerprint,
    final_holdout_input_json,
    final_holdout_json,
    release_for_oos_run,
    unlock_final_holdout,
)
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
_CREATED = datetime(2025, 1, 1, tzinfo=timezone.utc)


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
        "created_at": _CREATED,
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


def _inputs(**overrides: object) -> FinalHoldoutInputs:
    """Return the frozen final-evaluation inputs with overridable fields."""
    fields: dict[str, object] = {
        "test_set_id": "holdout-1",
        "dataset_fingerprint": _FINGERPRINT,
        "config_hash": "cfg-hash",
        "selection_fingerprint": "s" * 64,
        "code_version": "1.0.0",
    }
    fields.update(overrides)
    return FinalHoldoutInputs(**fields)  # type: ignore[arg-type]


def _unlock(**overrides: object) -> FinalHoldoutRelease:
    """Unlock the final holdout with overridable arguments."""
    fields: dict[str, object] = {
        "registration": _registration(),
        "current_stage": ValidationStatus.TEST_LOCKED,
        "responsibility": "Research Lead",
        "inputs": _inputs(),
        "unlocked_at": _AT,
    }
    fields.update(overrides)
    return unlock_final_holdout(**fields)  # type: ignore[arg-type]


class FinalHoldoutErrorTests(unittest.TestCase):
    """The dedicated error types and the unlock-once / frozen-selection rules."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(FinalHoldoutError, ValueError))

    def test_unlock_error_is_final_holdout_error(self) -> None:
        self.assertTrue(issubclass(FinalHoldoutUnlockError, FinalHoldoutError))

    def test_unlock_before_selection_frozen_rejected(self) -> None:
        # TUNING: the training/validation selection is not frozen yet.
        with self.assertRaises(FinalHoldoutUnlockError):
            _unlock(current_stage=ValidationStatus.TUNING)

    def test_second_unlock_rejected(self) -> None:
        release = _unlock()
        with self.assertRaises(FinalHoldoutUnlockError):
            unlock_final_holdout(
                release.registration,  # already read
                current_stage=ValidationStatus.TEST_LOCKED,
                responsibility="Research Lead",
                inputs=_inputs(),
                unlocked_at=_AT + timedelta(days=1),
            )


class FinalHoldoutInputsTests(unittest.TestCase):
    """The frozen research inputs consumed by the final evaluation."""

    def test_records_all_fields(self) -> None:
        inputs = _inputs()
        self.assertEqual(inputs.test_set_id, "holdout-1")
        self.assertEqual(inputs.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(inputs.config_hash, "cfg-hash")
        self.assertEqual(inputs.selection_fingerprint, "s" * 64)
        self.assertEqual(inputs.code_version, "1.0.0")

    def test_empty_test_set_id_rejected(self) -> None:
        with self.assertRaises(FinalHoldoutError):
            _inputs(test_set_id="")

    def test_empty_dataset_fingerprint_rejected(self) -> None:
        with self.assertRaises(FinalHoldoutError):
            _inputs(dataset_fingerprint="")

    def test_empty_config_hash_rejected(self) -> None:
        with self.assertRaises(FinalHoldoutError):
            _inputs(config_hash="")

    def test_empty_selection_fingerprint_rejected(self) -> None:
        with self.assertRaises(FinalHoldoutError):
            _inputs(selection_fingerprint="")

    def test_empty_code_version_rejected(self) -> None:
        with self.assertRaises(FinalHoldoutError):
            _inputs(code_version="")

    def test_readable(self) -> None:
        self.assertIn("final-evaluation inputs", _inputs().readable())


class HoldoutUnlockEventTests(unittest.TestCase):
    """The auditable unlock event value type."""

    def test_valid_event(self) -> None:
        event = HoldoutUnlockEvent(
            test_set_id="holdout-1",
            unlocked_at=_AT,
            stage=ValidationStatus.TEST_LOCKED,
            responsibility="Research Lead",
        )
        self.assertEqual(event.unlocked_at, _AT)
        self.assertEqual(event.stage, ValidationStatus.TEST_LOCKED)

    def test_empty_responsibility_rejected(self) -> None:
        with self.assertRaises(FinalHoldoutError):
            HoldoutUnlockEvent(
                test_set_id="holdout-1",
                unlocked_at=_AT,
                stage=ValidationStatus.TEST_LOCKED,
                responsibility="",
            )

    def test_naive_unlocked_at_rejected(self) -> None:
        with self.assertRaises(FinalHoldoutError):
            HoldoutUnlockEvent(
                test_set_id="holdout-1",
                unlocked_at=datetime(2026, 1, 1),
                stage=ValidationStatus.TEST_LOCKED,
                responsibility="Research Lead",
            )

    def test_non_test_locked_stage_rejected(self) -> None:
        with self.assertRaises(FinalHoldoutError):
            HoldoutUnlockEvent(
                test_set_id="holdout-1",
                unlocked_at=_AT,
                stage=ValidationStatus.TUNING,
                responsibility="Research Lead",
            )

    def test_readable(self) -> None:
        event = HoldoutUnlockEvent(
            test_set_id="holdout-1",
            unlocked_at=_AT,
            stage=ValidationStatus.TEST_LOCKED,
            responsibility="Research Lead",
        )
        self.assertIn("unlocked test set holdout-1", event.readable())


class UnlockExecutionTests(unittest.TestCase):
    """The final-holdout unlock orchestration."""

    def test_unlocks_at_test_locked(self) -> None:
        release = _unlock()
        self.assertIsNotNone(release.registration.first_read_at)
        self.assertEqual(release.registration.first_read_at, _AT)
        self.assertEqual(release.unlocked_at, _AT)

    def test_unlock_event_records_responsibility_and_stage(self) -> None:
        release = _unlock()
        self.assertEqual(release.responsibility, "Research Lead")
        self.assertEqual(release.stage, ValidationStatus.TEST_LOCKED)
        self.assertEqual(release.unlock_event.test_set_id, "holdout-1")

    def test_original_registration_unchanged(self) -> None:
        registration = _registration()
        release = unlock_final_holdout(
            registration,
            current_stage=ValidationStatus.TEST_LOCKED,
            responsibility="Research Lead",
            inputs=_inputs(),
            unlocked_at=_AT,
        )
        # immutability: the original registration is not read.
        self.assertIsNone(registration.first_read_at)
        self.assertIsNotNone(release.registration.first_read_at)

    def test_input_fingerprint_recorded_and_derivable(self) -> None:
        release = _unlock()
        self.assertEqual(release.input_fingerprint, final_holdout_input_fingerprint(_inputs()))
        self.assertEqual(
            release.input_fingerprint,
            hashlib.sha256(final_holdout_input_json(_inputs()).encode("utf-8")).hexdigest(),
        )

    def test_release_fingerprint_rederivable(self) -> None:
        release = _unlock()
        self.assertEqual(release.fingerprint, final_holdout_fingerprint(release))

    def test_unlock_at_evaluated_allowed(self) -> None:
        release = _unlock(current_stage=ValidationStatus.EVALUATED)
        self.assertEqual(release.stage, ValidationStatus.EVALUATED)
        self.assertIsNotNone(release.registration.first_read_at)

    def test_release_for_oos_run_inherits_context(self) -> None:
        run = _oos_run()
        release = release_for_oos_run(
            run,
            _registration(),
            current_stage=ValidationStatus.TEST_LOCKED,
            responsibility="Research Lead",
            config_hash="cfg-hash",
            selection_fingerprint="s" * 64,
            unlocked_at=_AT,
        )
        self.assertEqual(release.inputs.dataset_fingerprint, run.dataset_fingerprint)
        self.assertEqual(release.inputs.code_version, run.code_version)
        self.assertEqual(release.inputs.test_set_id, "holdout-1")
        self.assertEqual(release.input_fingerprint, final_holdout_input_fingerprint(release.inputs))


class FinalHoldoutReleaseValidationTests(unittest.TestCase):
    """The persisted release record invariants."""

    def test_requires_unlocked_registration(self) -> None:
        registration = _registration()  # not read
        event = HoldoutUnlockEvent(
            test_set_id="holdout-1",
            unlocked_at=_AT,
            stage=ValidationStatus.TEST_LOCKED,
            responsibility="Research Lead",
        )
        with self.assertRaises(FinalHoldoutError):
            FinalHoldoutRelease(
                unlock_event=event,
                registration=registration,
                inputs=_inputs(),
                input_fingerprint=final_holdout_input_fingerprint(_inputs()),
                fingerprint="x" * 64,
            )

    def test_test_set_id_mismatch_rejected(self) -> None:
        release = _unlock()
        with self.assertRaises(FinalHoldoutError):
            replace(release, inputs=_inputs(test_set_id="other"))

    def test_input_fingerprint_mismatch_rejected(self) -> None:
        release = _unlock()
        with self.assertRaises(FinalHoldoutError):
            replace(release, input_fingerprint="wrong")

    def test_unlocked_at_mismatch_rejected(self) -> None:
        release = _unlock()
        with self.assertRaises(FinalHoldoutError):
            replace(
                release,
                unlock_event=replace(release.unlock_event, unlocked_at=_AT + timedelta(days=1)),
            )

    def test_empty_fingerprint_rejected(self) -> None:
        release = _unlock()
        with self.assertRaises(FinalHoldoutError):
            replace(release, fingerprint="")

    def test_readable(self) -> None:
        release = _unlock()
        text = release.readable()
        self.assertIn("final holdout holdout-1", text)
        self.assertIn("TEST_LOCKED", text)
        self.assertIn("Research Lead", text)


class FingerprintTests(unittest.TestCase):
    """Stable, re-derivable, sensitive input and release fingerprints."""

    def test_input_fingerprint_sha256_hex(self) -> None:
        digest = final_holdout_input_fingerprint(_inputs())
        self.assertEqual(len(digest), 64)
        int(digest, 16)

    def test_input_fingerprint_stable_and_rederivable(self) -> None:
        first = final_holdout_input_fingerprint(_inputs())
        second = final_holdout_input_fingerprint(_inputs())
        self.assertEqual(first, second)
        self.assertEqual(first, final_holdout_input_fingerprint(_inputs()))

    def test_release_fingerprint_sha256_and_rederivable(self) -> None:
        release = _unlock()
        self.assertEqual(len(release.fingerprint), 64)
        self.assertEqual(release.fingerprint, final_holdout_fingerprint(release))

    def test_release_fingerprint_stable(self) -> None:
        self.assertEqual(_unlock().fingerprint, _unlock().fingerprint)

    def test_changes_with_responsibility(self) -> None:
        self.assertNotEqual(
            _unlock().fingerprint,
            _unlock(responsibility="Other Reviewer").fingerprint,
        )

    def test_changes_with_inputs(self) -> None:
        self.assertNotEqual(
            _unlock().fingerprint,
            _unlock(inputs=_inputs(selection_fingerprint="t" * 64)).fingerprint,
        )

    def test_changes_with_unlock_time(self) -> None:
        self.assertNotEqual(
            _unlock().fingerprint,
            _unlock(unlocked_at=_AT + timedelta(hours=1)).fingerprint,
        )

    def test_json_excludes_fingerprint_and_key_sorted(self) -> None:
        release = _unlock()
        serialized = final_holdout_json(release)
        self.assertNotIn('"fingerprint"', serialized)
        self.assertIn('"input_fingerprint"', serialized)
        self.assertIn('"first_read_at"', serialized)
        self.assertIn('"responsibility":"Research Lead"', serialized)
        payload = json.loads(serialized)
        self.assertEqual(
            serialized,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )
