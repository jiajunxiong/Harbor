"""Rolling backtest unit tests (MVP 3 / SP 3.44, TEST-ONLY).

Covers the rolling backtest building blocks of SP 3.31–3.34 against the five
acceptance dimensions (覆盖扩展/固定窗口、边界对齐、重叠拒绝、再训练日和空折叠):

- expanding (扩展窗口) and fixed-length (固定长度窗口) windows — SP 3.31
  geometry (train window grows vs constant ``train_length_days``; the OOS
  horizon tiles contiguously; validation sits immediately before each test
  segment; a tight split makes fold 0 reproduce the base split);
- boundary alignment (边界对齐) — SP 3.32 aligns start boundaries forward and
  end boundaries backward to tradable days per market, recording every
  market's actual dates;
- overlap rejection (重叠拒绝) — overlapping or gapped OOS folds, overlapping
  or touching split ranges and collapsed aligned windows are all rejected
  (reject, never assume);
- retraining dates (再训练日) — fold 0 always retrains, EVERY_FOLD retrains
  every fold, and QUARTERLY / ANNUAL retrain only when the training end
  crosses a new quarter / year, otherwise inheriting;
- empty folds (空折叠) — an empty fold sequence, a reversed fold range, an
  empty aligned sequence, an entirely non-trading alignment window and empty
  training / validation runs are all rejected.

The end-to-end SP 3.33 / SP 3.34 pipeline is exercised for both window modes
to confirm the geometry flows through training and validation without
re-fitting.
"""

import unittest
from dataclasses import replace
from datetime import date, timedelta

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
from harbor.core.fold_calendar_align import (
    CalendarAlignedSequence,
    CalendarAlignmentError,
    MarketAlignedDates,
    align_fold_boundaries,
)
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    ParameterSpace,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.performance_metrics import PerformanceMetrics
from harbor.core.rolling_train import (
    RollingTrainError,
    RollingTrainRun,
    run_rolling_training,
)
from harbor.core.rolling_validate import (
    RollingValidationError,
    RollingValidationRun,
    ValidationComponents,
    run_rolling_validation,
)
from harbor.core.rolling_window import (
    FoldSequence,
    RollingWindowError,
    build_walk_forward_folds,
)
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
    SplitBoundaryError,
    WalkForwardFold,
)

_FINGERPRINT = "f" * 64


def _add_days(day: date, days: int) -> date:
    """Return ``day`` shifted by ``days`` calendar days."""
    return day + timedelta(days=days)


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


def _sequence(**overrides: object) -> FoldSequence:
    """Build the SP 3.31 fold sequence with overridable arguments."""
    fields: dict[str, object] = {
        "split": _split(),
        "rolling": _rolling(),
        "dataset_fingerprint": _FINGERPRINT,
    }
    fields.update(overrides)
    return build_walk_forward_folds(**fields)  # type: ignore[arg-type]


def _calendar() -> MarketTradingCalendar:
    """A calendar with a Monday 2023-01-02 holiday in HK only (divergent US)."""
    return MarketTradingCalendar(
        holidays={Market.HK: frozenset({date(2023, 1, 2)}), Market.US: frozenset()}
    )


def _fold(fold_index: int = 0, **overrides: object) -> WalkForwardFold:
    """A single internally-valid walk-forward fold for value-level tests."""
    fields: dict[str, object] = {
        "fold_index": fold_index,
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 1),
        "validation_end": date(2022, 12, 31),
        "test_start": date(2023, 1, 1),
        "test_end": date(2023, 12, 31),
        "retrain_date": date(2021, 12, 31),
        "dataset_fingerprint": _FINGERPRINT,
    }
    fields.update(overrides)
    return WalkForwardFold(**fields)  # type: ignore[arg-type]


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


def _training_run(**overrides: object) -> RollingTrainRun:
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


def _validation_run(**overrides: object) -> RollingValidationRun:
    """Build the SP 3.34 rolling validation run with overridable arguments."""
    fields: dict[str, object] = {
        "training_run": _training_run(),
        "application_factory": _application,
        "compute_validation": _compute_validation,
    }
    fields.update(overrides)
    return run_rolling_validation(**fields)  # type: ignore[arg-type]


class ExpandingWindowUnitTests(unittest.TestCase):
    """扩展窗口: the training window grows and fold 0 reproduces the split."""

    def test_fold_zero_reproduces_base_split(self) -> None:
        sequence = _sequence()
        fold = sequence[0]
        split = _split()
        self.assertEqual(fold.train_start, split.train_start)
        self.assertEqual(fold.train_end, split.train_end)
        self.assertEqual(fold.validation_start, split.validation_start)
        self.assertEqual(fold.validation_end, split.validation_end)
        self.assertEqual(fold.test_start, split.test_start)
        # the test is tiled: fold 0 covers the first 365-day step segment.
        self.assertEqual(fold.test_end, _add_days(split.test_start, 364))

    def test_train_start_constant_across_folds(self) -> None:
        sequence = _sequence()
        starts = {fold.train_start for fold in sequence}
        self.assertEqual(starts, {date(2019, 1, 1)})

    def test_train_window_grows(self) -> None:
        sequence = _sequence()
        ends = [fold.train_end for fold in sequence]
        self.assertTrue(all(ends[i] < ends[i + 1] for i in range(len(ends) - 1)))

    def test_test_tiles_horizon_contiguously(self) -> None:
        sequence = _sequence()
        self.assertEqual(sequence.oos_start, date(2023, 1, 1))
        self.assertEqual(sequence.oos_end, date(2026, 12, 30))
        for previous, current in zip(sequence.folds, sequence.folds[1:]):
            self.assertEqual(current.test_start, _add_days(previous.test_end, 1))

    def test_validation_immediately_before_test(self) -> None:
        sequence = _sequence()
        for fold in sequence:
            self.assertEqual(fold.validation_end, _add_days(fold.test_start, -1))
            self.assertEqual(
                fold.validation_start,
                _add_days(fold.test_start, -365),
            )

    def test_fold_count(self) -> None:
        self.assertEqual(len(_sequence()), 4)


class FixedWindowUnitTests(unittest.TestCase):
    """固定长度窗口: a constant train length that shifts forward."""

    def _fixed(self, length: int) -> FoldSequence:
        return _sequence(rolling=_rolling(mode=RollingWindowMode.FIXED, train_length_days=length))

    def test_constant_train_length(self) -> None:
        sequence = self._fixed(500)
        lengths = {(fold.train_end - fold.train_start).days + 1 for fold in sequence}
        self.assertEqual(lengths, {500})

    def test_window_shifts_forward(self) -> None:
        sequence = self._fixed(500)
        starts = [fold.train_start for fold in sequence]
        self.assertTrue(all(starts[i] < starts[i + 1] for i in range(len(starts) - 1)))

    def test_never_overlaps_validation(self) -> None:
        sequence = self._fixed(500)
        for fold in sequence:
            self.assertLess(fold.train_end, fold.validation_start)

    def test_different_length_changes_windows(self) -> None:
        short = self._fixed(300)
        long = self._fixed(600)
        self.assertNotEqual(short[1].train_start, long[1].train_start)

    def test_fixed_without_length_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RollingWindowConfig(
                mode=RollingWindowMode.FIXED,
                train_length_days=None,
                step_days=365,
                retrain_frequency=RetrainFrequency.EVERY_FOLD,
            )


class BoundaryAlignmentUnitTests(unittest.TestCase):
    """边界对齐: start forward, end backward, every market's actual dates."""

    def test_start_boundaries_align_forward(self) -> None:
        aligned = align_fold_boundaries(
            _sequence(), markets=(Market.HK,), calendar=_calendar(), calendar_version="cal-1"
        )
        hk = aligned[0].dates_for(Market.HK)
        self.assertIsNotNone(hk)
        assert hk is not None
        # 2023-01-01 is a Sunday; 2023-01-02 is an HK Monday holiday → 2023-01-03.
        self.assertEqual(hk.test_start, date(2023, 1, 3))

    def test_end_boundaries_align_backward(self) -> None:
        aligned = align_fold_boundaries(
            _sequence(), markets=(Market.HK,), calendar=_calendar(), calendar_version="cal-1"
        )
        hk = aligned[0].dates_for(Market.HK)
        self.assertIsNotNone(hk)
        assert hk is not None
        # 2023-12-31 is a Sunday → the last trading day is Friday 2023-12-29.
        self.assertEqual(hk.test_end, date(2023, 12, 29))

    def test_cross_market_records_each_market(self) -> None:
        aligned = align_fold_boundaries(
            _sequence(),
            markets=(Market.HK, Market.US),
            calendar=_calendar(),
            calendar_version="cal-1",
        )
        hk = aligned[0].dates_for(Market.HK)
        us = aligned[0].dates_for(Market.US)
        self.assertIsNotNone(hk)
        self.assertIsNotNone(us)
        assert hk is not None
        assert us is not None
        # HK has a Monday 2023-01-02 holiday; US does not → divergent test starts.
        self.assertEqual(hk.test_start, date(2023, 1, 3))
        self.assertEqual(us.test_start, date(2023, 1, 2))

    def test_aligned_ranges_strictly_ordered(self) -> None:
        aligned = align_fold_boundaries(
            _sequence(), markets=(Market.HK,), calendar=_calendar(), calendar_version="cal-1"
        )
        for fold in aligned:
            dates = fold.dates_for(Market.HK)
            self.assertIsNotNone(dates)
            assert dates is not None
            self.assertLess(dates.train_end, dates.validation_start)
            self.assertLessEqual(dates.validation_end, dates.test_start)

    def test_calendar_version_recorded(self) -> None:
        aligned = align_fold_boundaries(
            _sequence(), markets=(Market.HK,), calendar=_calendar(), calendar_version="cal-1"
        )
        self.assertEqual(aligned.calendar_version, "cal-1")
        self.assertIn("cal-1", aligned.readable())


class OverlapRejectionUnitTests(unittest.TestCase):
    """重叠拒绝: overlapping OOS, split ranges and aligned windows are rejected."""

    def test_overlapping_oos_rejected(self) -> None:
        fold0 = _fold(fold_index=0)
        fold1 = _fold(fold_index=1, test_start=date(2023, 12, 1), test_end=date(2024, 11, 30))
        with self.assertRaises(RollingWindowError):
            FoldSequence(folds=(fold0, fold1), fingerprint="x" * 64)

    def test_gapped_oos_rejected(self) -> None:
        fold0 = _fold(fold_index=0)
        fold1 = _fold(fold_index=1, test_start=date(2024, 1, 2), test_end=date(2024, 12, 31))
        with self.assertRaises(RollingWindowError):
            FoldSequence(folds=(fold0, fold1), fingerprint="x" * 64)

    def test_split_overlap_rejected(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            EvaluationSplit(
                train_start=date(2019, 1, 1),
                train_end=date(2022, 6, 30),
                validation_start=date(2022, 1, 1),
                validation_end=date(2022, 12, 31),
                test_start=date(2023, 1, 1),
                test_end=date(2023, 12, 31),
            )

    def test_split_touching_rejected(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            EvaluationSplit(
                train_start=date(2019, 1, 1),
                train_end=date(2022, 1, 1),
                validation_start=date(2022, 1, 1),
                validation_end=date(2022, 12, 31),
                test_start=date(2023, 1, 1),
                test_end=date(2023, 12, 31),
            )

    def test_aligned_overlap_rejected(self) -> None:
        with self.assertRaises(CalendarAlignmentError):
            MarketAlignedDates(
                market=Market.HK,
                train_start=date(2019, 1, 1),
                train_end=date(2022, 6, 30),
                validation_start=date(2022, 1, 1),
                validation_end=date(2022, 12, 31),
                test_start=date(2023, 1, 1),
                test_end=date(2023, 12, 31),
            )

    def test_aligned_reversed_rejected(self) -> None:
        with self.assertRaises(CalendarAlignmentError):
            MarketAlignedDates(
                market=Market.HK,
                train_start=date(2022, 1, 1),
                train_end=date(2019, 1, 1),
                validation_start=date(2022, 1, 1),
                validation_end=date(2022, 12, 31),
                test_start=date(2023, 1, 1),
                test_end=date(2023, 12, 31),
            )


class RetrainDateUnitTests(unittest.TestCase):
    """再训练日: fold 0 always, EVERY_FOLD, and QUARTERLY / ANNUAL boundaries."""

    def test_fold_zero_always_retrains(self) -> None:
        sequence = _sequence()
        self.assertEqual(sequence[0].retrain_date, sequence[0].train_end)

    def test_every_fold_retrains(self) -> None:
        sequence = _sequence()
        for fold in sequence:
            self.assertEqual(fold.retrain_date, fold.train_end)

    def test_quarterly_retrains_on_quarter_boundaries(self) -> None:
        sequence = _sequence(
            split=_split(test_start=date(2023, 4, 1), test_end=date(2023, 12, 31)),
            rolling=_rolling(
                step_days=30,
                retrain_frequency=RetrainFrequency.QUARTERLY,
            ),
        )
        retrains = {fold.fold_index for fold in sequence if fold.retrain_date == fold.train_end}
        self.assertEqual(retrains, {0, 1, 4, 7})

    def test_annual_retrains_on_year_boundaries(self) -> None:
        sequence = _sequence(
            split=_split(test_start=date(2023, 4, 1), test_end=date(2024, 12, 31)),
            rolling=_rolling(
                step_days=120,
                retrain_frequency=RetrainFrequency.ANNUAL,
            ),
        )
        retrains = {fold.fold_index for fold in sequence if fold.retrain_date == fold.train_end}
        self.assertEqual(retrains, {0, 3})

    def test_retrain_dates_non_decreasing(self) -> None:
        sequence = _sequence(
            split=_split(test_start=date(2023, 4, 1), test_end=date(2023, 12, 31)),
            rolling=_rolling(
                step_days=30,
                retrain_frequency=RetrainFrequency.QUARTERLY,
            ),
        )
        dates = [fold.retrain_date for fold in sequence]
        for previous, current in zip(dates, dates[1:]):
            self.assertLessEqual(previous, current)

    def test_retrain_not_after_train_end(self) -> None:
        sequence = _sequence()
        for fold in sequence:
            self.assertLessEqual(fold.retrain_date, fold.train_end)


class EmptyFoldUnitTests(unittest.TestCase):
    """空折叠: empty sequences, reversed ranges and collapsed windows rejected."""

    def test_empty_fold_sequence_rejected(self) -> None:
        with self.assertRaises(RollingWindowError):
            FoldSequence(folds=(), fingerprint="x" * 64)

    def test_reversed_fold_range_rejected(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            _fold(train_start=date(2021, 12, 31), train_end=date(2019, 1, 1))

    def test_empty_aligned_sequence_rejected(self) -> None:
        with self.assertRaises(CalendarAlignmentError):
            CalendarAlignedSequence(
                folds=(),
                calendar_version="cal-1",
                fingerprint="x" * 64,
            )

    def test_entirely_non_trading_window_rejected(self) -> None:
        # fold 0's test window is a Saturday..Sunday weekend — collapses on alignment.
        sequence = FoldSequence(
            folds=(
                _fold(
                    test_start=date(2023, 1, 7),
                    test_end=date(2023, 1, 8),
                ),
            ),
            fingerprint="x" * 64,
        )
        with self.assertRaises(CalendarAlignmentError):
            align_fold_boundaries(sequence, markets=(Market.HK,), calendar=_calendar())

    def test_empty_training_run_rejected(self) -> None:
        with self.assertRaises(RollingTrainError):
            RollingTrainRun(
                results=(),
                market=Market.HK,
                dataset_fingerprint=_FINGERPRINT,
                code_version="1.0.0",
                fingerprint="x" * 64,
            )

    def test_empty_validation_run_rejected(self) -> None:
        with self.assertRaises(RollingValidationError):
            RollingValidationRun(
                results=(),
                dataset_fingerprint=_FINGERPRINT,
                code_version="1.0.0",
                fingerprint="x" * 64,
            )


class RollingPipelineUnitTests(unittest.TestCase):
    """Both window modes flow end-to-end through training and validation."""

    def test_expanding_window_pipeline(self) -> None:
        training = _training_run(sequence=_sequence())
        self.assertEqual(len(training), 4)
        validation = run_rolling_validation(
            training,
            application_factory=_application,
            compute_validation=_compute_validation,
        )
        self.assertEqual(len(validation), 4)

    def test_fixed_window_pipeline(self) -> None:
        sequence = _sequence(rolling=_rolling(mode=RollingWindowMode.FIXED, train_length_days=500))
        training = _training_run(sequence=sequence)
        self.assertEqual(len(training), 4)
        validation = run_rolling_validation(
            training,
            application_factory=_application,
            compute_validation=_compute_validation,
        )
        self.assertEqual(len(validation), 4)

    def test_validation_uses_frozen_fit(self) -> None:
        training = _training_run(sequence=_sequence())
        validation = _validation_run(training_run=training)
        for result in validation:
            self.assertEqual(
                result.application.fit_fingerprint,
                result.training.fit.fingerprint,
            )
            self.assertEqual(
                result.application.decision_date,
                result.fold.validation_end,
            )
