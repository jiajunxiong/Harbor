"""Fold out-of-sample execution tests (MVP 3 / SP 3.35).

Verifies that each fold runs the MVP 2 engine on its subsequent unseen (test)
interval with the SP 3.33 selected parameters, preserving the full backtest
run id and replay manifest, and that the SP 3.24 test-access guard gates every
execution: a fold denied access, outside the registered holdout, or without a
selected candidate is recorded as not executed rather than silently omitted.
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
from harbor.core.rolling_oos import (
    FoldOosResult,
    OosRunOutcome,
    RollingOosError,
    RollingOosRun,
    rolling_oos_fingerprint,
    rolling_oos_json,
    run_rolling_oos,
)
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


def _oos_run(**overrides: object) -> RollingOosRun:
    """Run the rolling OOS execution with overridable arguments."""
    fields: dict[str, object] = {
        "validation_run": _validation_run(),
        "guard": _guard(),
        "current_stage": ValidationStatus.TEST_LOCKED,
        "run_engine": _run_engine,
        "requested_at": _AT,
    }
    fields.update(overrides)
    return run_rolling_oos(**fields)  # type: ignore[arg-type]


class RollingOosErrorTests(unittest.TestCase):
    """The dedicated error type and the outcome record guards."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(RollingOosError, ValueError))

    def test_outcome_run_id_mismatch_rejected(self) -> None:
        with self.assertRaises(RollingOosError):
            OosRunOutcome(
                run_id="run-1",
                replay_manifest=_manifest(_sequence()[0], "run-other"),
            )


class AccessGuardIntegrationTests(unittest.TestCase):
    """The SP 3.24 guard gates every fold's OOS execution (deps 3.24)."""

    def test_all_folds_execute_at_test_locked(self) -> None:
        run = _oos_run()
        self.assertTrue(run.all_executed)
        self.assertEqual(run.executed_count, 4)

    def test_denied_before_test_locked(self) -> None:
        run = _oos_run(current_stage=ValidationStatus.TUNING)
        self.assertEqual(run.executed_count, 0)
        for result in run:
            self.assertFalse(result.executed)
            self.assertIn("not authorized", result.failure_reason or "")

    def test_audit_records_one_entry_per_fold(self) -> None:
        run = _oos_run()
        self.assertEqual(len(run.access_guard.audit), 4)
        for entry in run.access_guard.audit:
            self.assertTrue(entry.granted)

    def test_denied_folds_are_audited(self) -> None:
        run = _oos_run(current_stage=ValidationStatus.TUNING)
        self.assertEqual(len(run.access_guard.audit), 4)
        self.assertTrue(all(not entry.granted for entry in run.access_guard.audit))

    def test_no_registration_denies_every_fold(self) -> None:
        run = _oos_run(guard=AccessGuard())
        self.assertEqual(run.executed_count, 0)
        self.assertIn("no test set is registered", run[0].failure_reason or "")

    def test_fold_outside_registered_holdout_not_executed(self) -> None:
        small_split = EvaluationSplit(
            train_start=date(2019, 1, 1),
            train_end=date(2022, 12, 31),
            validation_start=date(2023, 1, 1),
            validation_end=date(2023, 12, 31),
            test_start=date(2024, 1, 1),
            test_end=date(2024, 12, 31),
        )
        run = _oos_run(guard=_guard(registration=_registration(split=small_split)))
        # fold 0 OOS ends 2023-12-31, before the holdout test starts 2024-01-01.
        self.assertFalse(run[0].executed)
        self.assertIn("outside the registered holdout", run[0].failure_reason or "")
        # fold 1 OOS [2024-01-01, 2024-12-30] is within the holdout.
        self.assertTrue(run[1].executed)


class OosExecutionTests(unittest.TestCase):
    """Selected parameters run the engine on the fold's unseen interval."""

    def test_run_id_recorded_per_executed_fold(self) -> None:
        run = _oos_run()
        for result in run:
            self.assertEqual(result.run_id, f"oos-run-{result.fold.fold_index}")

    def test_run_id_written_onto_the_fold(self) -> None:
        run = _oos_run()
        for result in run:
            self.assertEqual(result.fold.run_id, result.run_id)
            self.assertEqual(
                result.fold.fold_index,
                result.validation.fold.fold_index,
            )

    def test_replay_manifest_preserved(self) -> None:
        run = _oos_run()
        for result in run:
            self.assertIsNotNone(result.replay_manifest)
            assert result.replay_manifest is not None
            self.assertEqual(result.replay_manifest.run_id, result.run_id)

    def test_engine_receives_the_selected_trial(self) -> None:
        received: list[tuple[int, str]] = []

        def recording_engine(fold, selected):
            received.append((fold.fold_index, selected.trial_id))
            return _run_engine(fold, selected)

        run = _oos_run(run_engine=recording_engine)
        self.assertEqual(run.executed_count, 4)
        self.assertEqual(
            received,
            [
                (0, "fold-trial-0-3"),
                (1, "fold-trial-1-3"),
                (2, "fold-trial-2-3"),
                (3, "fold-trial-3-3"),
            ],
        )

    def test_no_selected_candidate_not_executed(self) -> None:
        # validation_samples below the pre-registered minimum excludes all.
        run = _oos_run(
            validation_run=_validation_run(training_run=_training_run(validation_samples=50))
        )
        self.assertEqual(run.executed_count, 0)
        self.assertIn("no selected candidate", run[0].failure_reason or "")


class FoldOosResultValidationTests(unittest.TestCase):
    """The per-fold record enforces executed / not-executed invariants."""

    def _executed(self) -> FoldOosResult:
        return _oos_run()[0]

    def test_run_id_without_manifest_rejected(self) -> None:
        result = self._executed()
        with self.assertRaises(RollingOosError):
            replace(result, replay_manifest=None)

    def test_manifest_without_run_id_rejected(self) -> None:
        result = self._executed()
        with self.assertRaises(RollingOosError):
            replace(result, run_id=None, failure_reason="boom")

    def test_not_executed_without_reason_rejected(self) -> None:
        result = self._executed()
        with self.assertRaises(RollingOosError):
            replace(result, run_id=None, replay_manifest=None, failure_reason=None)

    def test_executed_with_failure_reason_rejected(self) -> None:
        result = self._executed()
        with self.assertRaises(RollingOosError):
            replace(result, failure_reason="boom")

    def test_manifest_run_id_mismatch_rejected(self) -> None:
        result = self._executed()
        manifest = result.replay_manifest
        assert manifest is not None
        # keep the manifest's original run id, change the result's -> mismatch.
        with self.assertRaises(RollingOosError):
            replace(result, run_id="other")

    def test_manifest_not_covering_fold_test_rejected(self) -> None:
        result = self._executed()
        manifest = result.replay_manifest
        assert manifest is not None
        # shrink the manifest start past the fold's test start.
        with self.assertRaises(RollingOosError):
            replace(
                result,
                replay_manifest=replace(
                    manifest,
                    data_boundaries=DataQueryBoundaries(
                        start_date=date(2023, 6, 1),
                        end_date=manifest.data_boundaries.end_date,
                        data_cutoff=manifest.data_boundaries.end_date,
                    ),
                ),
            )

    def test_readable(self) -> None:
        run = _oos_run()
        self.assertIn("OOS run", run[0].readable())
        denied = _oos_run(current_stage=ValidationStatus.TUNING)
        self.assertIn("NOT executed", denied[0].readable())


class RollingOosRunValidationTests(unittest.TestCase):
    """The run value rejects an inconsistent, un-auditable record."""

    def test_empty_results_rejected(self) -> None:
        run = _oos_run()
        with self.assertRaises(RollingOosError):
            replace(run, results=())

    def test_non_sequential_fold_indices_rejected(self) -> None:
        run = _oos_run()
        second = replace(
            run[1],
            validation=replace(
                run[1].validation,
                training=replace(
                    run[1].validation.training,
                    fold=replace(run[1].fold, fold_index=2),
                ),
            ),
        )
        with self.assertRaises(RollingOosError):
            replace(run, results=(run[0], second))

    def test_empty_fingerprint_rejected(self) -> None:
        run = _oos_run()
        with self.assertRaises(RollingOosError):
            replace(run, fingerprint="")

    def test_len_iter_getitem(self) -> None:
        run = _oos_run()
        self.assertEqual(len(run), len(list(run)))
        self.assertEqual(list(run)[2].fold.fold_index, run[2].fold.fold_index)
        with self.assertRaises(IndexError):
            run[99]

    def test_oos_for_lookup_and_readable(self) -> None:
        run = _oos_run()
        self.assertIsNotNone(run.oos_for(0))
        self.assertIsNone(run.oos_for(99))
        self.assertIn("4/4 folds executed", run.readable())


class FingerprintTests(unittest.TestCase):
    """The run fingerprint is stable and re-derivable."""

    def test_fingerprint_is_sha256_hex(self) -> None:
        self.assertRegex(_oos_run().fingerprint, r"^[0-9a-f]{64}$")

    def test_fingerprint_rederivable(self) -> None:
        run = _oos_run()
        self.assertEqual(run.fingerprint, rolling_oos_fingerprint(run))

    def test_fingerprint_stable_across_equal_runs(self) -> None:
        self.assertEqual(_oos_run().fingerprint, _oos_run().fingerprint)

    def test_fingerprint_changes_with_run_id(self) -> None:
        def alt_engine(fold, selected):
            run_id = f"alt-{fold.fold_index}"
            return OosRunOutcome(run_id=run_id, replay_manifest=_manifest(fold, run_id))

        self.assertNotEqual(
            _oos_run(run_engine=alt_engine).fingerprint,
            _oos_run().fingerprint,
        )

    def test_fingerprint_changes_with_access_stage(self) -> None:
        self.assertNotEqual(
            _oos_run(current_stage=ValidationStatus.TUNING).fingerprint,
            _oos_run().fingerprint,
        )

    def test_fingerprint_changes_with_guard(self) -> None:
        self.assertNotEqual(
            _oos_run(guard=AccessGuard()).fingerprint,
            _oos_run().fingerprint,
        )

    def test_json_excludes_derived_fingerprint(self) -> None:
        payload = json.loads(rolling_oos_json(_oos_run()))
        self.assertNotIn("fingerprint", payload)
        self.assertIn("access_audit_fingerprint", payload)
        self.assertIn("results", payload)
        self.assertEqual(len(payload["results"]), 4)

    def test_json_is_key_sorted(self) -> None:
        payload = json.loads(rolling_oos_json(_oos_run()))
        self.assertEqual(
            list(payload.keys()),
            [
                "access_audit_fingerprint",
                "code_version",
                "dataset_fingerprint",
                "results",
            ],
        )
        first = payload["results"][0]
        self.assertEqual(
            list(first.keys()),
            ["failure_reason", "fold_index", "replay_fingerprint", "run_id"],
        )
        self.assertEqual(first["fold_index"], 0)
        self.assertEqual(first["run_id"], "oos-run-0")
        self.assertIsNone(first["failure_reason"])


if __name__ == "__main__":
    unittest.main()
