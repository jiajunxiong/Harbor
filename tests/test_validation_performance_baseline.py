"""Validation performance baseline tests (MVP 3 / SP 3.83).

Records the runtime and memory baseline at the target scale for the three
validation operations — 清单构建 (manifest construction, SP 3.6/3.7), 参数预算
(parameter budget trial registration, SP 3.15–3.18) and 滚动回测 (rolling OOS
pipeline, SP 3.33 → 3.35 → 3.37) — and guards against gross regressions with
generous, machine-tolerant ceilings (性能基线). Each operation runs once per
test class under ``tracemalloc`` + ``time.perf_counter``; the measured time and
peak traced memory are compared against the documented baseline
(``docs/performance_baseline.md``, SP 3.83 section).

Target scale: manifest over a 1-market / 9-component / 2192-day window; 100
trials registered within a declared budget; a 10-fold rolling OOS pipeline.
The ceilings are deliberately loose so the suite never flakes on a slow or
loaded machine while still catching a catastrophic regression. Self-contained
and deterministic (fixed Mock data, no randomness).
"""

import time
import tracemalloc
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from harbor.core.backtest_config import BenchmarkKind
from harbor.core.backtest_domain import Currency, Market, NetValue
from harbor.core.benchmark import BenchmarkLevel, BenchmarkSeries
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.dataset_fingerprint import dataset_fingerprint
from harbor.core.dataset_manifest import build_dataset_manifest, component_manifest
from harbor.core.drawdown_events import DrawdownConfig, DrawdownSeries
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.holdout_registry import register_test_set
from harbor.core.oos_concat import OosEquityPath, concatenate_fold_oos
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
    OosRunOutcome,
    RollingOosRun,
    run_rolling_oos,
)
from harbor.core.rolling_train import run_rolling_training
from harbor.core.rolling_validate import (
    ValidationComponents,
    run_rolling_validation,
)
from harbor.core.rolling_window import FoldSequence, build_walk_forward_folds
from harbor.core.test_access_guard import AccessGuard
from harbor.core.training_fit import build_training_fit
from harbor.core.trial_budget import TrialBudget
from harbor.core.trial_registry import TrialRegistry, build_trial_registry, trial_fingerprint
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
    DatasetManifest,
    EvaluationSplit,
    ManifestComponent,
    ParameterTrial,
    ValidationStatus,
)

HK = Market.HK
HKD = Currency.HKD

_MANIFEST_START = date(2019, 1, 1)
_MANIFEST_END = date(2024, 12, 31)
_TRIAL_COUNT = 100
_FOLD_COUNT = 10
_FP = "f" * 64

# Documented baseline ceilings (see docs/performance_baseline.md, SP 3.83).
_MANIFEST_RUNTIME_CEILING = 5.0
_MANIFEST_MEMORY_CEILING = 128 * 1024 * 1024  # 128 MiB
_BUDGET_RUNTIME_CEILING = 10.0
_BUDGET_MEMORY_CEILING = 256 * 1024 * 1024  # 256 MiB
_ROLLING_RUNTIME_CEILING = 30.0
_ROLLING_MEMORY_CEILING = 512 * 1024 * 1024  # 512 MiB


# ---------------------------------------------------------------------------
# Target-scale fixtures.
# ---------------------------------------------------------------------------


def _build_target_manifest() -> DatasetManifest:
    """清单构建: a 9-component manifest over the full 2019–2024 window."""
    start, end = _MANIFEST_START, _MANIFEST_END
    components = tuple(
        component_manifest(kind, "source", "1.0", start=start, end=end)
        for kind in ManifestComponent
    )
    manifest = build_dataset_manifest(
        markets=(HK,),
        base_currency=HKD,
        start_date=start,
        end_date=end,
        data_cutoff=end,
        config_hash="cfg-hash",
        code_version="1.0.0",
        calendar_version="cal-1",
        fx_source="mock",
        fingerprint="placeholder",
        components=components,
    )
    return replace(manifest, fingerprint=dataset_fingerprint(manifest))


def _target_space() -> ParameterSpace:
    """参数预算: a three-parameter space applying to HK and US."""
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


def _run_budget_trials() -> tuple[TrialRegistry, tuple[ParameterTrial, ...]]:
    """参数预算: register 100 distinct trials within the declared budget."""
    registry = build_trial_registry(
        space=_target_space(),
        budget=TrialBudget(max_trials=_TRIAL_COUNT, random_seed=42),
        dataset_fingerprint=_FP,
        code_version="1.0.0",
        market=HK,
        train_start=date(2019, 1, 1),
        train_end=date(2020, 12, 31),
        validation_start=date(2021, 1, 1),
        validation_end=date(2021, 12, 31),
        seed=42,
        constraints=(),
    )
    trials: list[ParameterTrial] = []
    for index in range(_TRIAL_COUNT):
        cash = 0.05 + 0.05 * (index % 5)  # 0.05..0.25, all on the 0.05 grid
        factor = round(1.0 - cash, 2)
        registry, trial = registry.register(
            {"cash_weight": cash, "factor_weight": factor, "lookback": 252},
            metric=0.10 + 0.001 * index,
        )
        trials.append(trial)
    return registry, tuple(trials)


def _split(**overrides: object) -> EvaluationSplit:
    """Return a tight split whose OOS horizon tiles into 10 folds (step 146)."""
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
    fields: dict[str, object] = {
        "mode": RollingWindowMode.EXPANDING,
        "train_length_days": None,
        "step_days": 146,
        "retrain_frequency": RetrainFrequency.EVERY_FOLD,
    }
    fields.update(overrides)
    return RollingWindowConfig(**fields)  # type: ignore[arg-type]


def _sequence(**overrides: object) -> FoldSequence:
    fields: dict[str, object] = {
        "split": _split(),
        "rolling": _rolling(),
        "dataset_fingerprint": _FP,
    }
    fields.update(overrides)
    return build_walk_forward_folds(**fields)  # type: ignore[arg-type]


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


def _evaluate(fold, parameters: dict[str, object]) -> float:
    return int(parameters["lookback"]) / 1000.0


def _fit_factory(train_start: date, train_end: date):
    return build_training_fit(
        fit_start=train_start,
        fit_end=train_end,
        dataset_fingerprint=_FP,
        code_version="1.0.0",
        fitted_state=(("lookback", 252.0),),
    )


def _application(fold_result, decision_date: date) -> ValidationApplication:
    application = ValidationApplication(
        fit_fingerprint=fold_result.fit.fingerprint,
        decision_date=decision_date,
        dataset_fingerprint=fold_result.fit.dataset_fingerprint,
        code_version=fold_result.fit.code_version,
        fingerprint="unfingerprinted",
        standardization=AppliedStandardization(
            decision_date=decision_date,
            scores=(("AAA", 0.5), ("BBB", -0.5)),
            method=StandardizationMethod.ZSCORE,
        ),
    )
    return replace(application, fingerprint=apply_fingerprint(application))


def _compute_validation(fold_result, application) -> ValidationComponents:
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
        market=HK,
        scores=(
            CoverageScore(
                market=HK,
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


def _registration():
    return register_test_set(
        test_set_id="holdout-1",
        split=_split(),
        config_hash="cfg-hash",
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )


def _guard() -> AccessGuard:
    return AccessGuard(registration=_registration())


def _manifest(fold, run_id: str) -> ReplayManifest:
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
    run_id = f"oos-run-{fold.fold_index}"
    return OosRunOutcome(run_id=run_id, replay_manifest=_manifest(fold, run_id))


def _net_values_for(fold) -> tuple[NetValue, ...]:
    factor = 1.0 + 0.5 * fold.fold_index
    return tuple(
        NetValue(
            as_of_date=fold.test_start + timedelta(days=index),
            currency=HKD,
            cash=1_000_000.0 * 0.5 * (1.0 + 0.001 * factor * index),
            securities_value=1_000_000.0 * 0.5 * (1.0 + 0.001 * factor * index),
        )
        for index in range((fold.test_end - fold.test_start).days + 1)
    )


def _run_rolling_pipeline() -> tuple[RollingOosRun, OosEquityPath]:
    """滚动回测: SP 3.33 → 3.35 → 3.37 over the 10-fold sequence."""
    training = run_rolling_training(
        sequence=_sequence(),
        space=_target_space(),
        budget=TrialBudget(max_trials=3, random_seed=42),
        market=HK,
        dataset_fingerprint=_FP,
        code_version="1.0.0",
        tuning=_tuning(),
        candidate_parameter_sets=_candidates(),
        fit_factory=_fit_factory,
        evaluate=_evaluate,
        constraints=(),
        validation_samples=200,
    )
    validation = run_rolling_validation(
        training,
        application_factory=_application,
        compute_validation=_compute_validation,
    )
    oos = run_rolling_oos(
        validation,
        guard=_guard(),
        current_stage=ValidationStatus.TEST_LOCKED,
        run_engine=_run_engine,
    )
    return oos, concatenate_fold_oos(oos, net_values_for=_net_values_for)


class ValidationPerformanceBaselineTests(unittest.TestCase):
    """Record and guard the three target-scale runtime and memory baselines."""

    @classmethod
    def setUpClass(cls) -> None:
        # 清单构建.
        tracemalloc.start()
        start = time.perf_counter()
        cls.manifest = _build_target_manifest()
        cls.manifest_elapsed = time.perf_counter() - start
        _, cls.manifest_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # 参数预算.
        tracemalloc.start()
        start = time.perf_counter()
        cls.registry, cls.trials = _run_budget_trials()
        cls.budget_elapsed = time.perf_counter() - start
        _, cls.budget_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # 滚动回测.
        tracemalloc.start()
        start = time.perf_counter()
        cls.oos, cls.path = _run_rolling_pipeline()
        cls.rolling_elapsed = time.perf_counter() - start
        _, cls.rolling_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    # --- 清单构建 (manifest construction) ---

    def test_manifest_scale_is_target(self) -> None:
        self.assertEqual(len(self.manifest.components), 9)
        self.assertEqual(self.manifest.start_date, _MANIFEST_START)
        self.assertEqual(self.manifest.end_date, _MANIFEST_END)

    def test_manifest_fingerprint_self_consistent(self) -> None:
        self.assertEqual(dataset_fingerprint(self.manifest), self.manifest.fingerprint)

    def test_manifest_runtime_baseline(self) -> None:
        self.assertLess(self.manifest_elapsed, _MANIFEST_RUNTIME_CEILING)

    def test_manifest_memory_baseline(self) -> None:
        self.assertLess(self.manifest_peak, _MANIFEST_MEMORY_CEILING)

    # --- 参数预算 (parameter budget) ---

    def test_budget_scale_is_target(self) -> None:
        self.assertEqual(len(self.trials), _TRIAL_COUNT)
        self.assertEqual(self.registry.used, _TRIAL_COUNT)
        self.assertTrue(self.registry.exhausted)

    def test_budget_trials_are_distinct_and_fingerprinted(self) -> None:
        self.assertEqual(len({trial.trial_id for trial in self.trials}), _TRIAL_COUNT)
        # The 5 cash levels produce 5 distinct input fingerprints (the SP 3.18
        # fingerprint excludes the trial id and metric by design).
        self.assertEqual(len({trial_fingerprint(trial) for trial in self.trials}), 5)
        self.assertEqual(self.trials[0].metric, 0.10)
        self.assertEqual(self.trials[-1].metric, 0.10 + 0.001 * (_TRIAL_COUNT - 1))

    def test_budget_runtime_baseline(self) -> None:
        self.assertLess(self.budget_elapsed, _BUDGET_RUNTIME_CEILING)

    def test_budget_memory_baseline(self) -> None:
        self.assertLess(self.budget_peak, _BUDGET_MEMORY_CEILING)

    # --- 滚动回测 (rolling OOS pipeline) ---

    def test_rolling_scale_is_target(self) -> None:
        self.assertEqual(len(self.oos.results), _FOLD_COUNT)
        self.assertEqual(self.path.fold_count, _FOLD_COUNT)

    def test_rolling_run_completes_and_executes_all_folds(self) -> None:
        self.assertTrue(self.oos.all_executed)
        self.assertEqual(self.oos.executed_count, _FOLD_COUNT)
        self.assertEqual(
            self.path.end_date,
            self.oos.results[-1].fold.test_end,
        )

    def test_rolling_runtime_baseline(self) -> None:
        self.assertLess(self.rolling_elapsed, _ROLLING_RUNTIME_CEILING)

    def test_rolling_memory_baseline(self) -> None:
        self.assertLess(self.rolling_peak, _ROLLING_MEMORY_CEILING)


if __name__ == "__main__":
    unittest.main()
