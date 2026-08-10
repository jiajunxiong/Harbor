"""OOS isolation integration tests (MVP 3 / SP 3.45, TEST-ONLY).

Runs the multi-fold pipeline (SP 3.33 training → SP 3.34 validation → SP 3.35
OOS execution → SP 3.37 concatenation) on fixed Mock data and confirms every
fold only reads its allowed time interval (固定 Mock 数据运行多折叠流程，确认每
折叠只读取其允许的时间区间).

A recording :class:`_MockMarketData` serves deterministic prices / fundamentals
and logs every read as ``(stage, start, end)``. The injected pipeline steps
read through it:

- training (SP 3.33 ``fit_factory``) reads prices over ``[train_start,
  train_end]`` — the fold's training window;
- validation (SP 3.34 ``application_factory`` + ``compute_validation``) reads
  fundamentals / prices over ``[validation_start, validation_end]`` — the
  fold's validation window;
- OOS (SP 3.35 ``run_engine``) reads prices over ``[test_start, test_end]`` —
  the fold's out-of-sample window.

The tests then assert every read is confined to the matching stage window and
that no training / validation read ever touches the fold's test interval
(anti-lookahead), and that a future (test-period) data change leaves the
training fits and registered trials unchanged. The OOS run, its replay
manifests, the access-guard audit and the concatenated path are also verified
end to end.
"""

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
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.holdout_registry import register_test_set
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
from harbor.core.rolling_oos import OosRunOutcome, RollingOosRun, run_rolling_oos
from harbor.core.rolling_train import RollingTrainRun, run_rolling_training
from harbor.core.rolling_validate import (
    RollingValidationRun,
    ValidationComponents,
    run_rolling_validation,
)
from harbor.core.rolling_window import FoldSequence, build_walk_forward_folds
from harbor.core.test_access_guard import AccessGuard, AccessKind
from harbor.core.training_fit import build_training_fit
from harbor.core.trial_budget import TrialBudget
from harbor.core.trial_registry import trial_fingerprint
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


def _each_day(start: date, end: date) -> tuple[date, ...]:
    """Return every calendar day in the inclusive range."""
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return tuple(days)


def _overlaps(first: tuple[date, date], second: tuple[date, date]) -> bool:
    """Return whether two inclusive intervals overlap."""
    return first[0] <= second[1] and second[0] <= first[1]


class _MockMarketData:
    """Fixed deterministic market data that records every read.

    ``reads`` logs each access as ``(stage, start, end)`` so the tests can
    prove each pipeline step only reads its allowed time interval. ``overrides``
    lets a test alter prices in a specific date range (e.g. the future test
    interval) to prove training never reads it.
    """

    def __init__(self, price_base: float = 50.0, rate: float = 0.001) -> None:
        self.price_base = price_base
        self.rate = rate
        self.overrides: dict[date, float] = {}
        self.reads: list[tuple[str, date, date]] = []

    def price(self, day: date) -> float:
        """Return the deterministic price for ``day`` (override-aware)."""
        if day in self.overrides:
            return self.overrides[day]
        return self.price_base * (1.0 + self.rate * day.toordinal())

    def prices(self, stage: str, start: date, end: date) -> dict[date, dict[str, float]]:
        """Serve per-day prices and record the read under ``stage``."""
        self.reads.append((stage, start, end))
        return {
            day: {"AAA": self.price(day), "BBB": self.price(day) * 2.0}
            for day in _each_day(start, end)
        }

    def fundamentals(self, stage: str, start: date, end: date) -> dict[date, dict[str, float]]:
        """Serve per-day fundamentals and record the read under ``stage``."""
        self.reads.append((stage, start, end))
        return {day: {"AAA": 1.0, "BBB": 2.0} for day in _each_day(start, end)}


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


def _evaluate(fold, parameters: dict[str, object]) -> float:
    """Deterministic validation metric: larger lookback scores higher."""
    return int(parameters["lookback"]) / 1000.0


def _make_fit_factory(data: _MockMarketData):
    """A training fit that reads prices over the training window (recorded)."""

    def fit_factory(train_start: date, train_end: date):
        prices = data.prices("train", train_start, train_end)
        average = sum(day["AAA"] for day in prices.values()) / max(1, len(prices))
        return build_training_fit(
            fit_start=train_start,
            fit_end=train_end,
            dataset_fingerprint=_FINGERPRINT,
            code_version="1.0.0",
            fitted_state=(("lookback", average),),
        )

    return fit_factory


def _run_training(
    data: _MockMarketData,
    *,
    sequence: FoldSequence | None = None,
) -> RollingTrainRun:
    """Run the SP 3.33 rolling training over the recording data."""
    return run_rolling_training(
        sequence=sequence if sequence is not None else _sequence(),
        space=_space(),
        budget=_budget(),
        market=Market.HK,
        dataset_fingerprint=_FINGERPRINT,
        code_version="1.0.0",
        tuning=_tuning(),
        candidate_parameter_sets=_candidates(),
        fit_factory=_make_fit_factory(data),
        evaluate=_evaluate,
        constraints=(),
        validation_samples=200,
    )


def _make_application_factory(data: _MockMarketData):
    """A validation application reading fundamentals over the validation window."""

    def application_factory(fold_result, decision_date: date) -> ValidationApplication:
        fold = fold_result.fold
        data.fundamentals("validation_fundamental", fold.validation_start, decision_date)
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

    return application_factory


def _make_compute_validation(data: _MockMarketData):
    """The four validation results, reading prices over the validation window."""

    def compute_validation(fold_result, application) -> ValidationComponents:
        fold = fold_result.fold
        data.prices("validation_price", fold.validation_start, fold.validation_end)
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

    return compute_validation


def _run_validation(training_run: RollingTrainRun, data: _MockMarketData) -> RollingValidationRun:
    """Run the SP 3.34 rolling validation over the recording data."""
    return run_rolling_validation(
        training_run,
        application_factory=_make_application_factory(data),
        compute_validation=_make_compute_validation(data),
    )


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


def _make_run_engine(data: _MockMarketData):
    """The MVP 2 engine reading prices over the fold's OOS interval (recorded)."""

    def run_engine(fold, selected) -> OosRunOutcome:
        data.prices("oos", fold.test_start, fold.test_end)
        run_id = f"oos-run-{fold.fold_index}"
        return OosRunOutcome(run_id=run_id, replay_manifest=_manifest(fold, run_id))

    return run_engine


def _run_oos(validation_run: RollingValidationRun, data: _MockMarketData) -> RollingOosRun:
    """Run the SP 3.35 rolling OOS execution over the recording data."""
    return run_rolling_oos(
        validation_run,
        guard=_guard(),
        current_stage=ValidationStatus.TEST_LOCKED,
        run_engine=_make_run_engine(data),
        requested_at=_AT,
    )


def _net_values_for(data: _MockMarketData):
    """Per-fold OOS net values, reading prices over the OOS interval (recorded)."""

    def net_values_for(fold) -> tuple[NetValue, ...]:
        days = tuple(data.prices("oos_net", fold.test_start, fold.test_end))
        factor = 1.0 + 0.5 * fold.fold_index
        return tuple(
            NetValue(
                as_of_date=day,
                currency=Currency.HKD,
                cash=1_000_000.0 * (1.0 + 0.001 * factor * index),
                securities_value=0.0,
            )
            for index, day in enumerate(days)
        )

    return net_values_for


class OosIsolationIntegrationTests(unittest.TestCase):
    """Full SP 3.33–3.37 pipeline on recording Mock data — per-fold isolation."""

    def setUp(self) -> None:
        self._data = _MockMarketData()
        self._folds = tuple(_sequence())
        self._training_run = _run_training(self._data)
        self._validation_run = _run_validation(self._training_run, self._data)
        self._oos_run = _run_oos(self._validation_run, self._data)
        self._path = concatenate_fold_oos(self._oos_run, net_values_for=_net_values_for(self._data))

    def test_training_reads_confined_to_training_windows(self) -> None:
        for fold in self._folds:
            self.assertIn(("train", fold.train_start, fold.train_end), self._data.reads)

    def test_validation_reads_confined_to_validation_windows(self) -> None:
        for fold in self._folds:
            self.assertIn(
                ("validation_fundamental", fold.validation_start, fold.validation_end),
                self._data.reads,
            )
            self.assertIn(
                ("validation_price", fold.validation_start, fold.validation_end),
                self._data.reads,
            )

    def test_oos_reads_confined_to_test_windows(self) -> None:
        for fold in self._folds:
            self.assertIn(("oos", fold.test_start, fold.test_end), self._data.reads)
            self.assertIn(("oos_net", fold.test_start, fold.test_end), self._data.reads)

    def test_training_never_reads_own_test_interval(self) -> None:
        # expanding windows legitimately include EARLIER folds' OOS data, so the
        # anti-lookahead property is per-fold: fold i never reads its OWN test.
        for fold in self._folds:
            self.assertLess(fold.train_end, fold.test_start)
            self.assertFalse(
                _overlaps((fold.train_start, fold.train_end), (fold.test_start, fold.test_end))
            )

    def test_validation_never_reads_own_test_interval(self) -> None:
        for fold in self._folds:
            self.assertLess(fold.validation_end, fold.test_start)
            self.assertFalse(
                _overlaps(
                    (fold.validation_start, fold.validation_end),
                    (fold.test_start, fold.test_end),
                )
            )

    def test_each_fold_reads_exactly_its_own_windows(self) -> None:
        expected: set[tuple[str, date, date]] = set()
        for fold in self._folds:
            expected.add(("train", fold.train_start, fold.train_end))
            expected.add(("validation_fundamental", fold.validation_start, fold.validation_end))
            expected.add(("validation_price", fold.validation_start, fold.validation_end))
            expected.add(("oos", fold.test_start, fold.test_end))
            expected.add(("oos_net", fold.test_start, fold.test_end))
        self.assertEqual(set(self._data.reads), expected)

    def test_one_result_per_fold_across_pipeline(self) -> None:
        self.assertEqual(len(self._training_run), 4)
        self.assertEqual(len(self._validation_run), 4)
        self.assertEqual(len(self._oos_run), 4)
        self.assertEqual(self._path.fold_count, 4)


class OosExecutionIntegrationTests(unittest.TestCase):
    """The SP 3.35 run, replay manifests, access audit and concatenated path."""

    def setUp(self) -> None:
        self._data = _MockMarketData()
        self._training_run = _run_training(self._data)
        self._validation_run = _run_validation(self._training_run, self._data)
        self._oos_run = _run_oos(self._validation_run, self._data)
        self._path = concatenate_fold_oos(self._oos_run, net_values_for=_net_values_for(self._data))

    def test_oos_run_executes_all_folds(self) -> None:
        self.assertTrue(self._oos_run.all_executed)
        self.assertEqual(self._oos_run.executed_count, 4)

    def test_replay_manifest_covers_each_fold_oos_interval(self) -> None:
        for result in self._oos_run:
            manifest = result.replay_manifest
            self.assertIsNotNone(manifest)
            assert manifest is not None
            self.assertEqual(manifest.data_boundaries.start_date, result.fold.test_start)
            self.assertEqual(manifest.data_boundaries.end_date, result.fold.test_end)

    def test_access_guard_authorizes_data_read_per_fold(self) -> None:
        audit = self._oos_run.access_guard.audit
        self.assertEqual(len(audit), len(self._oos_run.results))
        for entry in audit:
            self.assertEqual(entry.access_kind, AccessKind.DATA_READ)
            self.assertTrue(entry.granted)
            self.assertEqual(entry.stage, ValidationStatus.TEST_LOCKED)

    def test_oos_path_spans_full_horizon(self) -> None:
        self.assertEqual(self._path.start_date, self._oos_run.results[0].fold.test_start)
        self.assertEqual(self._path.end_date, self._oos_run.results[-1].fold.test_end)

    def test_oos_path_contiguous_across_folds(self) -> None:
        values = self._path.net_values
        for previous, current in zip(values, values[1:]):
            self.assertEqual(
                current.as_of_date,
                previous.as_of_date + timedelta(days=1),
            )
        self.assertEqual(
            self._path.fold_count,
            len(self._oos_run.results),
        )


class FutureDataInvarianceTests(unittest.TestCase):
    """Fold i never reads its own future — its fits/trials are future-invariant."""

    def _override_range(self, data: _MockMarketData, start: date, end: date, value: float) -> None:
        for day in _each_day(start, end):
            data.overrides[day] = value

    def test_each_fold_own_future_prices_do_not_change_its_fit(self) -> None:
        for index, fold in enumerate(_sequence()):
            data = _MockMarketData()
            base = _run_training(data)
            # fold's own validation + test interval is its future w.r.t. training.
            self._override_range(data, fold.validation_start, fold.test_end, 9999.0)
            modified = _run_training(data)
            self.assertEqual(
                base.results[index].fit.fingerprint,
                modified.results[index].fit.fingerprint,
            )

    def test_each_fold_own_future_prices_do_not_change_its_trials(self) -> None:
        for index, fold in enumerate(_sequence()):
            data = _MockMarketData()
            base = _run_training(data)
            self._override_range(data, fold.validation_start, fold.test_end, 9999.0)
            modified = _run_training(data)
            self.assertEqual(
                [trial_fingerprint(trial) for trial in base.results[index].trials],
                [trial_fingerprint(trial) for trial in modified.results[index].trials],
            )

    def test_validation_prices_do_not_change_fold_zero_fit(self) -> None:
        data = _MockMarketData()
        base = _run_training(data)
        fold = _sequence()[0]
        self._override_range(data, fold.validation_start, fold.validation_end, 7777.0)
        modified = _run_training(data)
        self.assertEqual(
            base.results[0].fit.fingerprint,
            modified.results[0].fit.fingerprint,
        )

    def test_training_window_price_changes_its_fit(self) -> None:
        # positive control: altering data inside fold 0's training window changes
        # fold 0's fit, proving the invariance comes from confinement, not from
        # the fit ignoring the data.
        data = _MockMarketData()
        base = _run_training(data)
        self._override_range(data, date(2019, 1, 1), date(2019, 1, 10), 1234.0)
        modified = _run_training(data)
        self.assertNotEqual(
            base.results[0].fit.fingerprint,
            modified.results[0].fit.fingerprint,
        )
