"""Fold chain integrity tests (MVP 3 / SP 3.36).

Verifies that every out-of-sample result links to its complete evidence chain
(折叠链路完整性): the data manifest (数据清单), fit snapshot (拟合快照),
parameter trial (参数试验), MVP 2 run and replay manifest (MVP 2 运行) and
report artifacts (报告产物). A fold missing any link is surfaced with exactly
which links are missing — never silently assumed complete.
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
from harbor.core.oos_chain import (
    FoldChain,
    OosChainError,
    OosChainIntegrity,
    oos_chain_fingerprint,
    oos_chain_json,
    verify_fold_chains,
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


def _report_artifact(fold) -> str:
    """Default per-fold report-artifact fingerprint."""
    return f"report-{fold.fold_index}"


def _integrity(**overrides: object) -> OosChainIntegrity:
    """Verify the fold chains with overridable arguments."""
    fields: dict[str, object] = {
        "oos_run": _oos_run(),
        "report_artifact_for": _report_artifact,
    }
    fields.update(overrides)
    return verify_fold_chains(**fields)  # type: ignore[arg-type]


class OosChainErrorTests(unittest.TestCase):
    """The dedicated error type and the chain record guards."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(OosChainError, ValueError))

    def test_trial_link_must_be_paired(self) -> None:
        with self.assertRaises(OosChainError):
            FoldChain(fold_index=0, trial_id="trial-1")

    def test_run_link_must_be_paired(self) -> None:
        with self.assertRaises(OosChainError):
            FoldChain(fold_index=0, run_id="run-1")

    def test_negative_fold_index_rejected(self) -> None:
        with self.assertRaises(OosChainError):
            FoldChain(fold_index=-1)


class CompleteChainTests(unittest.TestCase):
    """Every OOS result links to all five evidence kinds (SP 3.36 acceptance)."""

    def test_default_chains_are_all_complete(self) -> None:
        integrity = _integrity()
        self.assertTrue(integrity.complete)
        self.assertEqual(len(integrity.incomplete_chains), 0)
        for chain in integrity:
            self.assertTrue(chain.complete)
            self.assertEqual(chain.missing_links, ())

    def test_data_manifest_link(self) -> None:
        integrity = _integrity()
        for index, chain in enumerate(integrity):
            self.assertEqual(chain.dataset_fingerprint, _FINGERPRINT)
            self.assertEqual(chain.fold_index, index)

    def test_fit_snapshot_link(self) -> None:
        integrity = _integrity()
        for chain in integrity:
            self.assertTrue(chain.fit_fingerprint)
            self.assertRegex(chain.fit_fingerprint, r"^[0-9a-f]{64}$")

    def test_parameter_trial_link(self) -> None:
        integrity = _integrity()
        for index, chain in enumerate(integrity):
            self.assertEqual(chain.trial_id, f"fold-trial-{index}-3")
            self.assertRegex(chain.trial_fingerprint, r"^[0-9a-f]{64}$")

    def test_mvp2_run_link(self) -> None:
        integrity = _integrity()
        for index, chain in enumerate(integrity):
            self.assertEqual(chain.run_id, f"oos-run-{index}")
            self.assertTrue(chain.replay_fingerprint)

    def test_report_artifact_link(self) -> None:
        integrity = _integrity()
        for index, chain in enumerate(integrity):
            self.assertEqual(chain.report_artifact_fingerprint, f"report-{index}")

    def test_one_chain_per_fold(self) -> None:
        integrity = _integrity()
        self.assertEqual(len(integrity), 4)
        for index, chain in enumerate(integrity):
            self.assertEqual(chain.fold_index, index)


class MissingLinkTests(unittest.TestCase):
    """A missing link surfaces the exact gap instead of assuming completeness."""

    def test_missing_report_artifact(self) -> None:
        integrity = _integrity(report_artifact_for=None)
        self.assertFalse(integrity.complete)
        self.assertEqual(len(integrity.incomplete_chains), 4)
        self.assertEqual(
            integrity.missing_links_for(0),
            ("report_artifact",),
        )

    def test_not_executed_missing_run_and_report(self) -> None:
        integrity = _integrity(
            oos_run=_oos_run(current_stage=ValidationStatus.TUNING),
            report_artifact_for=None,
        )
        self.assertFalse(integrity.complete)
        # trials were still selected in SP 3.33; only the run + report are missing.
        self.assertEqual(
            integrity.missing_links_for(0),
            ("mvp2_run", "report_artifact"),
        )

    def test_no_selection_missing_trial(self) -> None:
        integrity = _integrity(
            oos_run=_oos_run(
                validation_run=_validation_run(training_run=_training_run(validation_samples=50))
            ),
        )
        self.assertFalse(integrity.complete)
        missing = integrity.missing_links_for(0)
        assert missing is not None
        self.assertIn("parameter_trial", missing)
        self.assertIn("mvp2_run", missing)

    def test_missing_links_for_absent_fold(self) -> None:
        integrity = _integrity()
        self.assertIsNone(integrity.missing_links_for(99))

    def test_readable_reports_counts(self) -> None:
        complete = _integrity()
        self.assertIn("4/4 folds with complete evidence chains", complete.readable())
        incomplete = _integrity(report_artifact_for=None)
        self.assertIn("0/4 folds with complete evidence chains", incomplete.readable())
        self.assertIn("chain missing", incomplete[0].readable())


class OosChainIntegrityValidationTests(unittest.TestCase):
    """The integrity report rejects an inconsistent, un-auditable record."""

    def _complete(self) -> OosChainIntegrity:
        return _integrity()

    def test_empty_chains_rejected(self) -> None:
        with self.assertRaises(OosChainError):
            replace(self._complete(), chains=())

    def test_non_sequential_fold_indices_rejected(self) -> None:
        integrity = self._complete()
        second = replace(integrity[1], fold_index=2)
        with self.assertRaises(OosChainError):
            replace(integrity, chains=(integrity[0], second))

    def test_inconsistent_dataset_fingerprint_rejected(self) -> None:
        integrity = self._complete()
        bad = replace(integrity[0], dataset_fingerprint="g" * 64)
        with self.assertRaises(OosChainError):
            replace(integrity, chains=(bad, integrity[1], integrity[2], integrity[3]))

    def test_empty_fingerprint_rejected(self) -> None:
        with self.assertRaises(OosChainError):
            replace(self._complete(), fingerprint="")

    def test_len_iter_getitem(self) -> None:
        integrity = self._complete()
        self.assertEqual(len(integrity), len(list(integrity)))
        self.assertEqual(list(integrity)[2].fold_index, integrity[2].fold_index)
        with self.assertRaises(IndexError):
            integrity[99]

    def test_chain_for_lookup(self) -> None:
        integrity = self._complete()
        self.assertIsNotNone(integrity.chain_for(0))
        self.assertIsNone(integrity.chain_for(99))


class FingerprintTests(unittest.TestCase):
    """The chain-report fingerprint is stable and re-derivable."""

    def test_fingerprint_is_sha256_hex(self) -> None:
        self.assertRegex(_integrity().fingerprint, r"^[0-9a-f]{64}$")

    def test_fingerprint_rederivable(self) -> None:
        integrity = _integrity()
        self.assertEqual(integrity.fingerprint, oos_chain_fingerprint(integrity))

    def test_fingerprint_stable_across_equal_reports(self) -> None:
        self.assertEqual(_integrity().fingerprint, _integrity().fingerprint)

    def test_fingerprint_changes_with_run(self) -> None:
        def alt_engine(fold, selected):
            run_id = f"alt-{fold.fold_index}"
            return OosRunOutcome(run_id=run_id, replay_manifest=_manifest(fold, run_id))

        self.assertNotEqual(
            _integrity(oos_run=_oos_run(run_engine=alt_engine)).fingerprint,
            _integrity().fingerprint,
        )

    def test_fingerprint_changes_with_report_artifact(self) -> None:
        self.assertNotEqual(
            _integrity(report_artifact_for=lambda fold: f"alt-{fold.fold_index}").fingerprint,
            _integrity().fingerprint,
        )

    def test_fingerprint_changes_with_access(self) -> None:
        self.assertNotEqual(
            _integrity(oos_run=_oos_run(current_stage=ValidationStatus.TUNING)).fingerprint,
            _integrity().fingerprint,
        )

    def test_json_excludes_derived_fingerprint(self) -> None:
        payload = json.loads(oos_chain_json(_integrity()))
        self.assertNotIn("fingerprint", payload)
        self.assertIn("dataset_fingerprint", payload)
        self.assertIn("chains", payload)
        self.assertEqual(len(payload["chains"]), 4)

    def test_json_is_key_sorted(self) -> None:
        payload = json.loads(oos_chain_json(_integrity()))
        self.assertEqual(
            list(payload.keys()),
            ["chains", "code_version", "dataset_fingerprint"],
        )
        first = payload["chains"][0]
        self.assertEqual(
            list(first.keys()),
            [
                "dataset_fingerprint",
                "fit_fingerprint",
                "fold_index",
                "replay_fingerprint",
                "report_artifact_fingerprint",
                "run_id",
                "trial_fingerprint",
                "trial_id",
            ],
        )
        self.assertEqual(first["fold_index"], 0)
        self.assertEqual(first["run_id"], "oos-run-0")
        self.assertEqual(first["trial_id"], "fold-trial-0-3")


if __name__ == "__main__":
    unittest.main()
