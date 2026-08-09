"""OOS net-value concatenation tests (MVP 3 / SP 3.37).

Verifies the non-overlapping fold OOS net-value series are concatenated in
time order into one equity path, and that aggregation is refused on overlap,
gap, currency mismatch, empty (unexecuted) folds and non-ascending series
(重叠、缺口和货币不一致时拒绝汇总).
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
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.holdout_registry import register_test_set
from harbor.core.oos_concat import (
    OosConcatError,
    OosEquityPath,
    concatenate_fold_oos,
    oos_concat_fingerprint,
    oos_concat_json,
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


def _series(
    start: date,
    end: date,
    currency: Currency = Currency.HKD,
    start_value: float = 1_000_000.0,
) -> tuple[NetValue, ...]:
    """Generate a daily net-value series over the inclusive range."""
    days = (end - start).days + 1
    return tuple(
        NetValue(
            as_of_date=start + timedelta(days=index),
            currency=currency,
            cash=start_value,
            securities_value=0.0,
        )
        for index in range(days)
    )


def _fold_net_values(fold, currency: Currency = Currency.HKD) -> tuple[NetValue, ...]:
    """Default per-fold OOS net values spanning the fold's test interval."""
    return _series(fold.test_start, fold.test_end, currency=currency)


def _concat(**overrides: object) -> OosEquityPath:
    """Concatenate the default OOS run with overridable arguments."""
    fields: dict[str, object] = {
        "oos_run": _oos_run(),
        "net_values_for": _fold_net_values,
    }
    fields.update(overrides)
    return concatenate_fold_oos(**fields)  # type: ignore[arg-type]


class OosConcatErrorTests(unittest.TestCase):
    """The dedicated error type."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(OosConcatError, ValueError))


class ConcatenationTests(unittest.TestCase):
    """Non-overlapping fold series concatenate in time order (SP 3.37)."""

    def test_path_spans_the_full_oos_horizon(self) -> None:
        path = _concat()
        self.assertEqual(path.start_date, date(2023, 1, 1))
        self.assertEqual(path.end_date, date(2025, 12, 31))

    def test_path_length_is_the_sum_of_fold_series(self) -> None:
        path = _concat()
        total = sum((fold.test_end - fold.test_start).days + 1 for fold in _sequence().folds)
        self.assertEqual(len(path), total)

    def test_net_values_strictly_ascending_and_contiguous(self) -> None:
        path = _concat()
        previous: date | None = None
        for net in path:
            if previous is not None:
                self.assertEqual(net.as_of_date, previous + timedelta(days=1))
            previous = net.as_of_date

    def test_fold_ranges_and_slicing(self) -> None:
        path = _concat()
        self.assertEqual(path.fold_count, 4)
        for index, fold in enumerate(_sequence().folds):
            segment = path.fold_net_values(index)
            self.assertEqual(segment[0].as_of_date, fold.test_start)
            self.assertEqual(segment[-1].as_of_date, fold.test_end)
            self.assertEqual(len(segment), (fold.test_end - fold.test_start).days + 1)

    def test_all_net_values_share_the_path_currency(self) -> None:
        path = _concat()
        self.assertEqual(path.currency, Currency.HKD)
        for net in path:
            self.assertEqual(net.currency, Currency.HKD)

    def test_path_iteration_and_indexing(self) -> None:
        path = _concat()
        self.assertEqual(len(path), len(list(path)))
        self.assertEqual(list(path)[0].as_of_date, path[0].as_of_date)
        with self.assertRaises(IndexError):
            path[len(path)]

    def test_readable(self) -> None:
        path = _concat()
        self.assertIn("OOS path 2023-01-01..2025-12-31", path.readable())
        self.assertIn("HKD", path.readable())


class RejectionTests(unittest.TestCase):
    """Overlap, gap, currency mismatch, empty and non-ascending are rejected."""

    def test_overlap_rejected(self) -> None:
        def overlapping(fold):
            if fold.fold_index == 1:
                # starts on the previous fold's last day.
                return _series(fold.test_start - timedelta(days=1), fold.test_end)
            return _fold_net_values(fold)

        with self.assertRaises(OosConcatError) as ctx:
            _concat(net_values_for=overlapping)
        self.assertIn("overlaps", str(ctx.exception))

    def test_gap_rejected(self) -> None:
        def gapped(fold):
            if fold.fold_index == 1:
                # skips the day after the previous fold's end.
                return _series(fold.test_start + timedelta(days=1), fold.test_end)
            return _fold_net_values(fold)

        with self.assertRaises(OosConcatError) as ctx:
            _concat(net_values_for=gapped)
        self.assertIn("gap", str(ctx.exception))

    def test_currency_mismatch_rejected(self) -> None:
        def usd_fold(fold):
            if fold.fold_index == 1:
                return _fold_net_values(fold, currency=Currency.USD)
            return _fold_net_values(fold)

        with self.assertRaises(OosConcatError) as ctx:
            _concat(net_values_for=usd_fold)
        self.assertIn("currency", str(ctx.exception))

    def test_empty_fold_series_rejected(self) -> None:
        def empty_fold(fold):
            if fold.fold_index == 1:
                return ()
            return _fold_net_values(fold)

        with self.assertRaises(OosConcatError) as ctx:
            _concat(net_values_for=empty_fold)
        self.assertIn("no net values", str(ctx.exception))

    def test_non_ascending_within_fold_rejected(self) -> None:
        def duplicated(fold):
            if fold.fold_index == 1:
                series = _fold_net_values(fold)
                return series[:2] + series[1:]
            return _fold_net_values(fold)

        with self.assertRaises(OosConcatError):
            _concat(net_values_for=duplicated)

    def test_mixed_currency_within_fold_rejected(self) -> None:
        def mixed(fold):
            if fold.fold_index == 1:
                series = _fold_net_values(fold)
                bad = replace(series[0], currency=Currency.USD)
                return (bad,) + series[1:]
            return _fold_net_values(fold)

        with self.assertRaises(OosConcatError):
            _concat(net_values_for=mixed)


class OosEquityPathValidationTests(unittest.TestCase):
    """The path value rejects an inconsistent, un-auditable record."""

    def _path(self) -> OosEquityPath:
        return _concat()

    def test_empty_net_values_rejected(self) -> None:
        with self.assertRaises(OosConcatError):
            replace(self._path(), net_values=())

    def test_non_ascending_net_values_rejected(self) -> None:
        path = self._path()
        duplicated = replace(path[1], as_of_date=path[0].as_of_date)
        with self.assertRaises(OosConcatError):
            replace(path, net_values=(path[0], duplicated) + path.net_values[2:])

    def test_mixed_currency_rejected(self) -> None:
        path = self._path()
        bad = replace(path[0], currency=Currency.USD)
        with self.assertRaises(OosConcatError):
            replace(path, net_values=(bad,) + path.net_values[1:])

    def test_non_contiguous_fold_ranges_rejected(self) -> None:
        path = self._path()
        with self.assertRaises(OosConcatError):
            replace(path, fold_ranges=((0, 10), (12, 20)))

    def test_out_of_range_fold_segment_rejected(self) -> None:
        path = self._path()
        with self.assertRaises(OosConcatError):
            replace(path, fold_ranges=((0, 10), (11, len(path.net_values))))

    def test_empty_fingerprint_rejected(self) -> None:
        with self.assertRaises(OosConcatError):
            replace(self._path(), fingerprint="")

    def test_fold_net_values_absent_fold(self) -> None:
        path = self._path()
        self.assertEqual(path.fold_net_values(99), ())


class FingerprintTests(unittest.TestCase):
    """The path fingerprint is stable and re-derivable."""

    def test_fingerprint_is_sha256_hex(self) -> None:
        self.assertRegex(_concat().fingerprint, r"^[0-9a-f]{64}$")

    def test_fingerprint_rederivable(self) -> None:
        path = _concat()
        self.assertEqual(path.fingerprint, oos_concat_fingerprint(path))

    def test_fingerprint_stable_across_equal_paths(self) -> None:
        self.assertEqual(_concat().fingerprint, _concat().fingerprint)

    def test_fingerprint_changes_with_values(self) -> None:
        def alt_values(fold):
            return _series(fold.test_start, fold.test_end, start_value=2_000_000.0)

        self.assertNotEqual(
            _concat(net_values_for=alt_values).fingerprint,
            _concat().fingerprint,
        )

    def test_fingerprint_changes_with_currency(self) -> None:
        self.assertNotEqual(
            _concat(
                net_values_for=lambda fold: _fold_net_values(fold, currency=Currency.USD)
            ).fingerprint,
            _concat().fingerprint,
        )

    def test_json_excludes_derived_fingerprint(self) -> None:
        payload = json.loads(oos_concat_json(_concat()))
        self.assertNotIn("fingerprint", payload)
        self.assertIn("currency", payload)
        self.assertIn("net_values", payload)
        self.assertIn("fold_ranges", payload)

    def test_json_is_key_sorted(self) -> None:
        payload = json.loads(oos_concat_json(_concat()))
        self.assertEqual(
            list(payload.keys()),
            ["code_version", "currency", "dataset_fingerprint", "fold_ranges", "net_values"],
        )
        first_net = payload["net_values"][0]
        self.assertEqual(
            list(first_net.keys()),
            ["as_of_date", "cash", "currency", "fees_paid", "securities_value"],
        )
        self.assertEqual(first_net["as_of_date"], "2023-01-01")


if __name__ == "__main__":
    unittest.main()
