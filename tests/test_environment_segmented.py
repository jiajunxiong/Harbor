"""Environment-segmented performance tests (MVP 3 / SP 3.50).

Verifies that, for every pre-registered SP 3.48 regime, the OOS days the SP
3.49 attribution classified into it are broken out with the strategy and
benchmark returns, drawdown, risk (volatility / Sharpe), turnover, costs and
data-coverage score — and that a segment with insufficient samples is
explicitly labeled (分环境输出策略与基准的收益、回撤、风险、换手、成本和覆盖评
分；样本不足明确标注).
"""

import json
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
from harbor.core.drawdown_events import DrawdownConfig, DrawdownSeries
from harbor.core.environment_attribution import (
    EnvironmentAttributionReport,
    attribute_environment,
)
from harbor.core.environment_segmented import (
    EnvironmentSegmentedError,
    EnvironmentSegmentedPerformance,
    EnvironmentSegmentPerformance,
    compute_environment_segments,
    environment_segments_fingerprint,
    environment_segments_json,
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
from harbor.core.oos_concat import concatenate_fold_oos
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
_OOS_START = date(2023, 1, 1)
_OOS_END = date(2026, 12, 30)


def _each_day(start: date, end: date) -> tuple[date, ...]:
    """Return every calendar day in the inclusive range (path is contiguous)."""
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return tuple(days)


def _trading_days(fold) -> tuple[date, ...]:
    """The fold's OOS days (every calendar day, so folds concatenate)."""
    return _each_day(fold.test_start, fold.test_end)


def _day_ordinal(as_of: date) -> int:
    """The day's zero-based ordinal within the OOS horizon."""
    return (as_of - _OOS_START).days


def _measure(dimension: EnvironmentDimension, as_of: date, window_days: int) -> float | None:
    """Deterministic regime classification: bull always, high-vol on 1/4 days."""
    if dimension is EnvironmentDimension.TREND:
        return 0.05  # >= 0 → bull_market
    if dimension is EnvironmentDimension.VOLATILITY:
        return 0.30 if _day_ordinal(as_of) % 4 == 0 else 0.10  # high_volatility
    if dimension is EnvironmentDimension.LIQUIDITY:
        return 0.02  # <= 0.05 → low_liquidity
    return 0.03  # >= 0.02 → fx_volatile


def _make_net_values_for(return_cycle: tuple[float, ...]):
    """A per-fold net-value series that continues one continuous path."""

    def net_values_for(fold) -> tuple[NetValue, ...]:
        days = _trading_days(fold)
        prior_days = (fold.test_start - _OOS_START).days
        value = 1_000_000.0
        for i in range(prior_days):
            value *= 1.0 + return_cycle[i % len(return_cycle)]
        values: list[NetValue] = []
        for index, day in enumerate(days):
            values.append(
                NetValue(as_of_date=day, currency=Currency.HKD, cash=value, securities_value=0.0)
            )
            value *= 1.0 + return_cycle[(prior_days + index) % len(return_cycle)]
        return tuple(values)

    return net_values_for


_net_values_for = _make_net_values_for((0.001,))


def _benchmark(day: date) -> float:
    """A constant daily benchmark return of 5 basis points."""
    return 0.0005


def _turnover(day: date) -> float:
    """A constant daily turnover of 1%."""
    return 0.01


def _cost(day: date) -> float:
    """A constant daily cost of 5.0."""
    return 5.0


def _coverage(day: date) -> float:
    """A constant daily coverage score of 95%."""
    return 95.0


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


def _attribution(**overrides: object) -> EnvironmentAttributionReport:
    """Compute the SP 3.49 environment attribution with overridable arguments."""
    fields: dict[str, object] = {
        "oos_run": _oos_run(),
        "definition_set": default_environment_set(),
        "measure_for": _measure,
        "trading_days_for": _trading_days,
    }
    fields.update(overrides)
    return attribute_environment(**fields)  # type: ignore[arg-type]


def _path(**overrides: object):
    """Concatenate the OOS equity path with overridable arguments."""
    fields: dict[str, object] = {
        "oos_run": _oos_run(),
        "net_values_for": _net_values_for,
    }
    fields.update(overrides)
    return concatenate_fold_oos(**fields)  # type: ignore[arg-type]


def _report(**overrides: object) -> EnvironmentSegmentedPerformance:
    """Compute the SP 3.50 segmented performance with overridable arguments."""
    fields: dict[str, object] = {
        "attribution": _attribution(),
        "path": _path(),
        "definition_set": default_environment_set(),
        "min_samples": 20,
        "periods_per_year": 252.0,
        "risk_free_rate": 0.0,
        "benchmark_return_for": _benchmark,
        "turnover_for": _turnover,
        "cost_for": _cost,
        "coverage_for": _coverage,
    }
    fields.update(overrides)
    return compute_environment_segments(**fields)  # type: ignore[arg-type]


def _segment(**overrides: object) -> EnvironmentSegmentPerformance:
    """A minimal sufficient segment for value-level tests."""
    fields: dict[str, object] = {
        "dimension": EnvironmentDimension.TREND,
        "regime_name": "bull_market",
        "day_count": 100,
        "sufficient": True,
        "insufficient_reason": None,
        "strategy_return": 0.05,
        "strategy_drawdown": 0.02,
        "strategy_volatility": 0.10,
        "strategy_sharpe": 0.5,
        "benchmark_return": 0.02,
        "excess_return": 0.03,
        "turnover": 0.5,
        "costs": 100.0,
        "coverage_pct": 90.0,
    }
    fields.update(overrides)
    return EnvironmentSegmentPerformance(**fields)  # type: ignore[arg-type]


def _insufficient(**overrides: object) -> EnvironmentSegmentPerformance:
    """A minimal insufficient segment for value-level tests."""
    fields: dict[str, object] = {
        "dimension": EnvironmentDimension.TREND,
        "regime_name": "bear_market",
        "day_count": 0,
        "sufficient": False,
        "insufficient_reason": "insufficient samples: 0 OOS day(s) below the 20-day minimum",
        "strategy_return": None,
        "strategy_drawdown": None,
        "strategy_volatility": None,
        "strategy_sharpe": None,
        "benchmark_return": None,
        "excess_return": None,
        "turnover": None,
        "costs": None,
        "coverage_pct": None,
    }
    fields.update(overrides)
    return EnvironmentSegmentPerformance(**fields)  # type: ignore[arg-type]


def _report_direct(**overrides: object) -> EnvironmentSegmentedPerformance:
    """A minimal report assembled from value-level segments."""
    fields: dict[str, object] = {
        "segments": (_insufficient(), _segment()),
        "definition_version": "1.0",
        "definition_fingerprint": "a" * 64,
        "dataset_fingerprint": _FINGERPRINT,
        "code_version": "1.0.0",
        "min_samples": 20,
        "fingerprint": "x" * 64,
    }
    fields.update(overrides)
    return EnvironmentSegmentedPerformance(**fields)  # type: ignore[arg-type]


class EnvironmentSegmentedErrorTests(unittest.TestCase):
    """The dedicated error type."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(EnvironmentSegmentedError, ValueError))

    def test_non_positive_min_samples_rejected(self) -> None:
        with self.assertRaises(EnvironmentSegmentedError):
            _report(min_samples=0)


class EnvironmentSegmentPerformanceTests(unittest.TestCase):
    """One (dimension, regime) segment and its metrics invariants."""

    def test_valid_sufficient_segment(self) -> None:
        segment = _segment()
        self.assertTrue(segment.sufficient)
        self.assertIsNone(segment.insufficient_reason)
        self.assertEqual(segment.excess_return, 0.03)

    def test_valid_insufficient_segment(self) -> None:
        segment = _insufficient()
        self.assertFalse(segment.sufficient)
        self.assertIn("insufficient samples", segment.insufficient_reason)

    def test_insufficient_with_metrics_rejected(self) -> None:
        with self.assertRaises(EnvironmentSegmentedError):
            _insufficient(strategy_return=0.05)

    def test_sufficient_with_reason_rejected(self) -> None:
        with self.assertRaises(EnvironmentSegmentedError):
            _segment(insufficient_reason="boom")

    def test_excess_without_returns_rejected(self) -> None:
        with self.assertRaises(EnvironmentSegmentedError):
            _segment(strategy_return=None, benchmark_return=None, excess_return=0.03)

    def test_excess_required_when_both_present(self) -> None:
        with self.assertRaises(EnvironmentSegmentedError):
            _segment(excess_return=None)

    def test_excess_must_equal_difference(self) -> None:
        with self.assertRaises(EnvironmentSegmentedError):
            _segment(excess_return=0.99)

    def test_sharpe_without_volatility_rejected(self) -> None:
        with self.assertRaises(EnvironmentSegmentedError):
            _segment(strategy_volatility=None, strategy_sharpe=0.5)

    def test_negative_day_count_rejected(self) -> None:
        with self.assertRaises(EnvironmentSegmentedError):
            _segment(day_count=-1)

    def test_empty_regime_name_rejected(self) -> None:
        with self.assertRaises(EnvironmentSegmentedError):
            _segment(regime_name="")

    def test_readable(self) -> None:
        self.assertIn("trend/bull_market", _segment().readable())


class EnvironmentSegmentedPerformanceTests(unittest.TestCase):
    """The cross-regime segmented performance report."""

    def test_valid_report(self) -> None:
        report = _report_direct()
        self.assertEqual(len(report), 2)

    def test_empty_segments_rejected(self) -> None:
        with self.assertRaises(EnvironmentSegmentedError):
            _report_direct(segments=())

    def test_unsorted_segments_rejected(self) -> None:
        with self.assertRaises(EnvironmentSegmentedError):
            _report_direct(segments=(_segment(), _insufficient()))

    def test_zero_min_samples_rejected(self) -> None:
        with self.assertRaises(EnvironmentSegmentedError):
            _report_direct(min_samples=0)

    def test_empty_definition_version_rejected(self) -> None:
        with self.assertRaises(EnvironmentSegmentedError):
            _report_direct(definition_version="")

    def test_len_iter_getitem(self) -> None:
        report = _report()
        self.assertEqual(report[0].dimension, EnvironmentDimension.FX)
        self.assertEqual(
            [s.regime_name for s in report],
            ["fx_volatile", "low_liquidity", "bear_market", "bull_market", "high_volatility"],
        )

    def test_segment_lookup(self) -> None:
        report = _report()
        self.assertIsNotNone(report.segment(EnvironmentDimension.TREND, "bull_market"))
        self.assertIsNotNone(report.segment(EnvironmentDimension.VOLATILITY, "high_volatility"))
        self.assertIsNone(report.segment(EnvironmentDimension.TREND, "missing_regime"))

    def test_counts(self) -> None:
        report = _report()
        self.assertEqual(report.insufficient_count, 1)
        self.assertEqual(report.sufficient_count, 4)
        self.assertEqual(report.day_count, 4745)

    def test_readable(self) -> None:
        self.assertIn("5 segments", _report().readable())


class ComputeEnvironmentSegmentsTests(unittest.TestCase):
    """Every pre-registered regime segmented with per-day metrics."""

    def test_all_pre_registered_regimes_reported(self) -> None:
        report = _report()
        self.assertEqual(len(report), 5)
        for dimension, name in (
            (EnvironmentDimension.TREND, "bull_market"),
            (EnvironmentDimension.TREND, "bear_market"),
            (EnvironmentDimension.VOLATILITY, "high_volatility"),
            (EnvironmentDimension.LIQUIDITY, "low_liquidity"),
            (EnvironmentDimension.FX, "fx_volatile"),
        ):
            self.assertIsNotNone(report.segment(dimension, name))

    def test_segment_day_counts(self) -> None:
        report = _report()
        self.assertEqual(report.segment(EnvironmentDimension.TREND, "bull_market").day_count, 1460)
        self.assertEqual(
            report.segment(EnvironmentDimension.LIQUIDITY, "low_liquidity").day_count, 1460
        )
        self.assertEqual(report.segment(EnvironmentDimension.FX, "fx_volatile").day_count, 1460)
        self.assertEqual(
            report.segment(EnvironmentDimension.VOLATILITY, "high_volatility").day_count, 365
        )
        self.assertEqual(report.segment(EnvironmentDimension.TREND, "bear_market").day_count, 0)

    def test_bull_strategy_metrics_exact(self) -> None:
        bull = _report().segment(EnvironmentDimension.TREND, "bull_market")
        self.assertIsNotNone(bull)
        assert bull is not None
        self.assertAlmostEqual(bull.strategy_return, 1.001**1459 - 1.0, places=6)
        self.assertAlmostEqual(bull.strategy_volatility, 0.0, places=6)
        self.assertIsNone(bull.strategy_sharpe)  # zero volatility → no Sharpe
        self.assertAlmostEqual(bull.strategy_drawdown, 0.0, places=6)  # monotonic

    def test_high_volatility_strategy_return(self) -> None:
        hv = _report().segment(EnvironmentDimension.VOLATILITY, "high_volatility")
        self.assertIsNotNone(hv)
        assert hv is not None
        self.assertAlmostEqual(hv.strategy_return, 1.001**364 - 1.0, places=6)

    def test_benchmark_return_and_excess(self) -> None:
        bull = _report().segment(EnvironmentDimension.TREND, "bull_market")
        assert bull is not None
        self.assertAlmostEqual(bull.benchmark_return, 1.0005**1460 - 1.0, places=6)
        assert bull.strategy_return is not None
        self.assertAlmostEqual(
            bull.excess_return, bull.strategy_return - bull.benchmark_return, places=9
        )

    def test_turnover_costs_coverage(self) -> None:
        bull = _report().segment(EnvironmentDimension.TREND, "bull_market")
        assert bull is not None
        self.assertAlmostEqual(bull.turnover, 14.6, places=6)
        self.assertAlmostEqual(bull.costs, 7300.0, places=6)
        self.assertAlmostEqual(bull.coverage_pct, 95.0, places=6)

    def test_insufficient_segment_labeled(self) -> None:
        bear = _report().segment(EnvironmentDimension.TREND, "bear_market")
        self.assertIsNotNone(bear)
        assert bear is not None
        self.assertFalse(bear.sufficient)
        self.assertIn("insufficient samples", bear.insufficient_reason)
        self.assertIn("0 OOS day(s)", bear.insufficient_reason)
        self.assertIsNone(bear.strategy_return)
        self.assertIsNone(bear.benchmark_return)
        self.assertIsNone(bear.turnover)
        self.assertIsNone(bear.costs)
        self.assertIsNone(bear.coverage_pct)

    def test_all_insufficient_when_min_samples_high(self) -> None:
        report = _report(min_samples=100000)
        self.assertEqual(report.insufficient_count, 5)
        self.assertEqual(report.sufficient_count, 0)

    def test_report_context(self) -> None:
        report = _report()
        definition_set = default_environment_set()
        self.assertEqual(report.definition_version, definition_set.version)
        self.assertEqual(report.definition_fingerprint, definition_set.fingerprint)
        self.assertEqual(report.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(report.code_version, "1.0.0")
        self.assertEqual(report.min_samples, 20)

    def test_definition_mismatch_rejected(self) -> None:
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
            ),
        )
        with self.assertRaises(EnvironmentSegmentedError):
            _report(definition_set=custom)

    def test_path_dataset_mismatch_rejected(self) -> None:
        mismatched = replace(_attribution(), dataset_fingerprint="d" * 64)
        with self.assertRaises(EnvironmentSegmentedError):
            _report(attribution=mismatched)

    def test_varying_returns_produce_risk_metrics(self) -> None:
        report = _report(
            path=_path(net_values_for=_make_net_values_for((0.002, -0.001, 0.003, 0.001)))
        )
        bull = report.segment(EnvironmentDimension.TREND, "bull_market")
        self.assertIsNotNone(bull)
        assert bull is not None
        self.assertIsNotNone(bull.strategy_return)
        assert bull.strategy_return is not None
        self.assertGreater(bull.strategy_return, 0.0)
        self.assertIsNotNone(bull.strategy_volatility)
        assert bull.strategy_volatility is not None
        self.assertGreater(bull.strategy_volatility, 0.0)
        self.assertIsNotNone(bull.strategy_sharpe)
        assert bull.strategy_sharpe is not None
        self.assertGreater(bull.strategy_sharpe, 0.0)
        self.assertIsNotNone(bull.strategy_drawdown)
        assert bull.strategy_drawdown is not None
        self.assertGreater(bull.strategy_drawdown, 0.0)


class FingerprintTests(unittest.TestCase):
    """Stable, re-derivable, sensitive segmented-performance fingerprints."""

    def test_sha256_hex(self) -> None:
        digest = _report().fingerprint
        self.assertEqual(len(digest), 64)
        int(digest, 16)

    def test_rederivable(self) -> None:
        report = _report()
        self.assertEqual(report.fingerprint, environment_segments_fingerprint(report))

    def test_stable(self) -> None:
        self.assertEqual(_report().fingerprint, _report().fingerprint)

    def test_changes_with_measurement(self) -> None:
        def bear_measure(dimension, as_of, window_days):
            if dimension is EnvironmentDimension.TREND:
                return -0.05
            return _measure(dimension, as_of, window_days)

        self.assertNotEqual(
            _report().fingerprint,
            _report(attribution=_attribution(measure_for=bear_measure)).fingerprint,
        )

    def test_changes_with_benchmark(self) -> None:
        def higher_benchmark(day):
            return 0.001

        self.assertNotEqual(
            _report().fingerprint,
            _report(benchmark_return_for=higher_benchmark).fingerprint,
        )

    def test_changes_with_min_samples(self) -> None:
        self.assertNotEqual(_report().fingerprint, _report(min_samples=30).fingerprint)

    def test_json_excludes_fingerprint_and_key_sorted(self) -> None:
        report = _report()
        serialized = environment_segments_json(report)
        self.assertNotIn('"fingerprint"', serialized)
        self.assertIn('"definition_version":"1.0"', serialized)
        self.assertIn('"min_samples":20', serialized)
        payload = json.loads(serialized)
        self.assertEqual(
            serialized,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )
