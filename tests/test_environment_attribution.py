"""Historical environment attribution tests (MVP 3 / SP 3.49).

Verifies every OOS trading day and fold records its market environment label —
the active SP 3.48 pre-registered regimes — and the reason a label is missing
when the measurement is unavailable (为每个 OOS 交易日和折叠记录市场环境标签及标
签缺失原因).
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
from harbor.core.environment_attribution import (
    EnvironmentAttributionError,
    EnvironmentAttributionReport,
    EnvironmentLabel,
    FoldEnvironmentAttribution,
    attribute_environment,
    environment_attribution_fingerprint,
    environment_attribution_json,
)
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.holdout_registry import register_test_set
from harbor.core.market_environment import (
    EnvironmentComparison,
    EnvironmentDimension,
    build_environment_set,
    default_environment_set,
    define_regime,
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
from harbor.core.trading_calendar import MarketTradingCalendar
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
_MEASURE_CUTOFF = date(2023, 2, 1)


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


def _calendar() -> MarketTradingCalendar:
    """A calendar with no market holidays (weekends only)."""
    return MarketTradingCalendar(holidays={Market.HK: frozenset(), Market.US: frozenset()})


def _trading_days(fold) -> tuple[date, ...]:
    """The fold's OOS trading days."""
    return tuple(_calendar().trading_days(Market.HK, fold.test_start, fold.test_end))


def _measure(dimension: EnvironmentDimension, as_of: date, window_days: int) -> float | None:
    """Deterministic measurement: unmeasurable before the cutoff, else active."""
    if as_of < _MEASURE_CUTOFF:
        return None
    if dimension is EnvironmentDimension.TREND:
        return 0.05
    if dimension is EnvironmentDimension.VOLATILITY:
        return 0.30
    if dimension is EnvironmentDimension.LIQUIDITY:
        return 0.02
    return 0.03


def _attribution(**overrides: object) -> EnvironmentAttributionReport:
    """Compute the environment attribution with overridable arguments."""
    fields: dict[str, object] = {
        "oos_run": _oos_run(),
        "definition_set": default_environment_set(),
        "measure_for": _measure,
        "trading_days_for": _trading_days,
    }
    fields.update(overrides)
    return attribute_environment(**fields)  # type: ignore[arg-type]


def _label(**overrides: object) -> EnvironmentLabel:
    """A minimal environment label for value-level tests."""
    fields: dict[str, object] = {
        "as_of": date(2023, 3, 1),
        "fold_index": 0,
        "dimension": EnvironmentDimension.TREND,
        "regime_names": ("bull_market",),
        "measured_value": 0.05,
        "missing_reason": None,
    }
    fields.update(overrides)
    return EnvironmentLabel(**fields)  # type: ignore[arg-type]


def _fold_attribution(**overrides: object) -> FoldEnvironmentAttribution:
    """A minimal fold attribution for value-level tests."""
    fields: dict[str, object] = {
        "fold_index": 0,
        "labels": (
            _label(as_of=date(2023, 3, 1)),
            _label(as_of=date(2023, 3, 2), dimension=EnvironmentDimension.FX),
        ),
    }
    fields.update(overrides)
    return FoldEnvironmentAttribution(**fields)  # type: ignore[arg-type]


class EnvironmentAttributionErrorTests(unittest.TestCase):
    """The dedicated error type."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(EnvironmentAttributionError, ValueError))


class EnvironmentLabelTests(unittest.TestCase):
    """One (day, dimension) label and its measured / missing invariants."""

    def test_valid_measured_label(self) -> None:
        label = _label()
        self.assertEqual(label.regime_names, ("bull_market",))
        self.assertEqual(label.measured_value, 0.05)
        self.assertIsNone(label.missing_reason)

    def test_valid_missing_label(self) -> None:
        label = _label(
            regime_names=(),
            measured_value=None,
            missing_reason="cannot measure trend",
        )
        self.assertIsNone(label.measured_value)
        self.assertEqual(label.missing_reason, "cannot measure trend")

    def test_measured_with_missing_reason_rejected(self) -> None:
        with self.assertRaises(EnvironmentAttributionError):
            _label(missing_reason="boom")

    def test_missing_without_reason_rejected(self) -> None:
        with self.assertRaises(EnvironmentAttributionError):
            _label(regime_names=(), measured_value=None, missing_reason=None)

    def test_missing_with_regimes_rejected(self) -> None:
        with self.assertRaises(EnvironmentAttributionError):
            _label(measured_value=None, missing_reason="boom")

    def test_negative_fold_index_rejected(self) -> None:
        with self.assertRaises(EnvironmentAttributionError):
            _label(fold_index=-1)

    def test_readable(self) -> None:
        measured = _label().readable()
        self.assertIn("bull_market", measured)
        missing = _label(regime_names=(), measured_value=None, missing_reason="boom").readable()
        self.assertIn("boom", missing)


class FoldEnvironmentAttributionTests(unittest.TestCase):
    """One fold's ordered per-day labels."""

    def test_valid_fold_attribution(self) -> None:
        fold = _fold_attribution()
        self.assertEqual(len(fold.labels), 2)

    def test_empty_labels_rejected(self) -> None:
        with self.assertRaises(EnvironmentAttributionError):
            _fold_attribution(labels=())

    def test_label_wrong_fold_rejected(self) -> None:
        with self.assertRaises(EnvironmentAttributionError):
            _fold_attribution(labels=(_label(fold_index=1),))

    def test_unsorted_labels_rejected(self) -> None:
        with self.assertRaises(EnvironmentAttributionError):
            _fold_attribution(
                labels=(
                    _label(as_of=date(2023, 3, 2)),
                    _label(as_of=date(2023, 3, 1)),
                )
            )

    def test_days_property(self) -> None:
        fold = _fold_attribution()
        self.assertEqual(fold.days, (date(2023, 3, 1), date(2023, 3, 2)))

    def test_labels_for_lookup(self) -> None:
        fold = _fold_attribution()
        self.assertEqual(len(fold.labels_for(date(2023, 3, 1))), 1)
        self.assertEqual(fold.labels_for(date(2023, 4, 1)), ())

    def test_missing_count(self) -> None:
        fold = _fold_attribution(
            labels=(
                _label(),
                _label(
                    as_of=date(2023, 3, 2),
                    regime_names=(),
                    measured_value=None,
                    missing_reason="boom",
                ),
            )
        )
        self.assertEqual(fold.missing_count, 1)

    def test_readable(self) -> None:
        self.assertIn("fold 0", _fold_attribution().readable())


class ReportTests(unittest.TestCase):
    """The cross-fold attribution report."""

    def test_valid_report(self) -> None:
        report = _attribution()
        self.assertEqual(len(report), 4)

    def test_empty_folds_rejected(self) -> None:
        with self.assertRaises(EnvironmentAttributionError):
            EnvironmentAttributionReport(
                folds=(),
                definition_version="1.0",
                definition_fingerprint="a" * 64,
                dataset_fingerprint=_FINGERPRINT,
                code_version="1.0.0",
                fingerprint="x" * 64,
            )

    def test_non_sequential_folds_rejected(self) -> None:
        with self.assertRaises(EnvironmentAttributionError):
            EnvironmentAttributionReport(
                folds=(
                    _fold_attribution(fold_index=0),
                    _fold_attribution(fold_index=2),
                ),
                definition_version="1.0",
                definition_fingerprint="a" * 64,
                dataset_fingerprint=_FINGERPRINT,
                code_version="1.0.0",
                fingerprint="x" * 64,
            )

    def test_len_iter_getitem(self) -> None:
        report = _attribution()
        self.assertEqual(report[0].fold_index, 0)
        self.assertEqual([fold.fold_index for fold in report], [0, 1, 2, 3])

    def test_aggregate_counts(self) -> None:
        report = _attribution()
        self.assertGreater(report.day_count, 0)
        self.assertGreater(report.label_count, 0)
        self.assertGreater(report.missing_count, 0)

    def test_readable(self) -> None:
        self.assertIn("4 folds", _attribution().readable())


class AttributionTests(unittest.TestCase):
    """Every OOS day and fold carries a label or a missing reason."""

    def test_every_fold_has_attribution(self) -> None:
        report = _attribution()
        self.assertEqual([fold.fold_index for fold in report], [0, 1, 2, 3])

    def test_every_oos_day_labeled(self) -> None:
        report = _attribution()
        oos = _oos_run()
        for fold_index, fold in enumerate(report):
            expected_days = len(_trading_days(oos[fold_index].validation.fold))
            self.assertEqual(len(fold.days), expected_days)

    def test_label_per_dimension(self) -> None:
        report = _attribution()
        first_day = report[0].days[0]
        labels = report[0].labels_for(first_day)
        self.assertEqual(len(labels), 4)
        dimensions = {label.dimension for label in labels}
        self.assertEqual(
            dimensions,
            {
                EnvironmentDimension.TREND,
                EnvironmentDimension.VOLATILITY,
                EnvironmentDimension.LIQUIDITY,
                EnvironmentDimension.FX,
            },
        )

    def test_measured_regime_names(self) -> None:
        report = _attribution()
        day = report[3].days[0]
        labels = {label.dimension: label for label in report[3].labels_for(day)}
        self.assertEqual(labels[EnvironmentDimension.TREND].regime_names, ("bull_market",))
        self.assertEqual(
            labels[EnvironmentDimension.VOLATILITY].regime_names,
            ("high_volatility",),
        )
        self.assertEqual(
            labels[EnvironmentDimension.LIQUIDITY].regime_names,
            ("low_liquidity",),
        )
        self.assertEqual(labels[EnvironmentDimension.FX].regime_names, ("fx_volatile",))

    def test_missing_label_records_reason(self) -> None:
        report = _attribution()
        fold_zero = report[0]
        missing = [label for label in fold_zero.labels if label.measured_value is None]
        self.assertTrue(missing)
        for label in missing:
            self.assertIsNotNone(label.missing_reason)
            assert label.missing_reason is not None
            self.assertIn("cannot measure", label.missing_reason)

    def test_report_context(self) -> None:
        report = _attribution()
        definition_set = default_environment_set()
        self.assertEqual(report.definition_version, definition_set.version)
        self.assertEqual(report.definition_fingerprint, definition_set.fingerprint)
        self.assertEqual(report.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(report.code_version, "1.0.0")

    def test_labels_for_lookup(self) -> None:
        report = _attribution()
        first_day = report[0].days[0]
        self.assertEqual(len(report[0].labels_for(first_day)), 4)

    def test_missing_count_aggregate(self) -> None:
        report = _attribution()
        self.assertEqual(
            report.missing_count,
            sum(fold.missing_count for fold in report),
        )

    def test_definition_change_missing_reason(self) -> None:
        # a measurement None always yields a missing label, never a fabricated one.
        report = _attribution()
        missing = [label for label in report[0].labels if label.measured_value is None]
        self.assertTrue(all(label.regime_names == () for label in missing))


class FingerprintTests(unittest.TestCase):
    """Stable, re-derivable, sensitive attribution fingerprints."""

    def test_sha256_hex(self) -> None:
        digest = _attribution().fingerprint
        self.assertEqual(len(digest), 64)
        int(digest, 16)

    def test_rederivable(self) -> None:
        report = _attribution()
        self.assertEqual(report.fingerprint, environment_attribution_fingerprint(report))

    def test_stable(self) -> None:
        self.assertEqual(_attribution().fingerprint, _attribution().fingerprint)

    def test_changes_with_measurement(self) -> None:
        def different_measure(dimension, as_of, window_days):
            if dimension is EnvironmentDimension.TREND:
                return -0.05
            return _measure(dimension, as_of, window_days)

        self.assertNotEqual(
            _attribution().fingerprint,
            _attribution(measure_for=different_measure).fingerprint,
        )

    def test_changes_with_definition(self) -> None:
        custom = build_environment_set(
            version="2.0",
            source="pre-registered",
            regimes=(
                define_regime(
                    "strong_bull",
                    dimension=EnvironmentDimension.TREND,
                    comparison=EnvironmentComparison.AT_OR_ABOVE,
                    threshold=0.05,
                    window_days=63,
                ),
                define_regime(
                    "high_volatility",
                    dimension=EnvironmentDimension.VOLATILITY,
                    comparison=EnvironmentComparison.AT_OR_ABOVE,
                    threshold=0.20,
                    window_days=63,
                ),
            ),
        )
        self.assertNotEqual(
            _attribution().fingerprint,
            _attribution(definition_set=custom).fingerprint,
        )

    def test_json_excludes_fingerprint_and_key_sorted(self) -> None:
        report = _attribution()
        serialized = environment_attribution_json(report)
        self.assertNotIn('"fingerprint"', serialized)
        self.assertIn('"definition_version":"1.0"', serialized)
        payload = json.loads(serialized)
        self.assertEqual(
            serialized,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )
