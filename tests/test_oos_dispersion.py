"""Fold dispersion analysis tests (MVP 3 / SP 3.39).

Verifies per-fold returns, drawdown, turnover, coverage score and failure
reason are all output, and that the summary exposes the return spread and the
worst fold alongside the average — so unstable folds are not masked by an
average (不以平均值掩盖不稳定折叠).
"""

import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType

from harbor.core.backtest_config import BenchmarkKind
from harbor.core.backtest_domain import Currency, Market, NetValue
from harbor.core.benchmark import BenchmarkLevel, BenchmarkSeries
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.drawdown_events import DrawdownConfig, DrawdownSeries
from harbor.core.exposure import ExposurePoint, ExposureSeries
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.holdout_registry import register_test_set
from harbor.core.oos_concat import OosEquityPath, concatenate_fold_oos
from harbor.core.oos_dispersion import (
    FoldDispersion,
    OosDispersionError,
    OosDispersionReport,
    compute_fold_dispersion,
    oos_dispersion_fingerprint,
    oos_dispersion_json,
)
from harbor.core.oos_performance import (
    OosPerformanceReport,
    compute_oos_metrics,
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
from harbor.core.trade_metrics import TradeStats
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


def _varied_growth_series(fold, rate: float = 0.001) -> tuple[NetValue, ...]:
    """A fold's OOS net values with a fold-dependent drift (distinct returns)."""
    factor = 1.0 + 0.5 * fold.fold_index
    days = (fold.test_end - fold.test_start).days + 1
    return tuple(
        NetValue(
            as_of_date=fold.test_start + timedelta(days=index),
            currency=Currency.HKD,
            cash=1_000_000.0 * (1.0 + rate * factor * index),
            securities_value=0.0,
        )
        for index in range(days)
    )


def _varied_path(rate: float = 0.001) -> OosEquityPath:
    """Concatenate the OOS run using the varied net-value series."""
    return concatenate_fold_oos(
        _oos_run(),
        net_values_for=lambda fold: _varied_growth_series(fold, rate=rate),
    )


def _trade_stats(path: OosEquityPath) -> TradeStats:
    """Deterministic trade/turnover/cost stub."""
    return TradeStats(
        fill_count=4,
        buy_count=2,
        sell_count=2,
        round_trip_count=2,
        win_count=1,
        win_rate=0.5,
        average_holding_days=63.0,
        turnover=0.4,
        total_fees_base=120.0,
        slippage_cost_base=40.0,
        unfilled_count=0,
        refused_reasons=MappingProxyType({}),
    )


def _exposure_point(as_of: date, cash_exposure: float = 0.5) -> ExposurePoint:
    """A minimal exposure point for one day."""
    return ExposurePoint(
        as_of=as_of,
        base_currency=Currency.HKD,
        total_value=1_000_000.0,
        cash_exposure=cash_exposure,
        market_exposure=MappingProxyType({Market.HK: 0.5}),
        currency_exposure=MappingProxyType({Currency.HKD: 1.0}),
        symbol_exposure=MappingProxyType({}),
        industry_exposure=None,
    )


def _exposure(path: OosEquityPath) -> ExposureSeries:
    """A deterministic two-day exposure series over the OOS horizon."""
    return ExposureSeries(
        points=(
            _exposure_point(path.start_date),
            _exposure_point(path.end_date),
        )
    )


def _benchmark_return(path: OosEquityPath) -> float:
    """Deterministic benchmark total return over the OOS horizon."""
    return 0.02


def _performance_report(**overrides: object) -> OosPerformanceReport:
    """Build the SP 3.38 performance report on the varied path."""
    fields: dict[str, object] = {
        "path": _varied_path(),
        "trade_stats_for": _trade_stats,
        "exposure_for": _exposure,
        "benchmark_return_for": _benchmark_return,
    }
    fields.update(overrides)
    return compute_oos_metrics(**fields)  # type: ignore[arg-type]


def _turnover(fold) -> float:
    """Per-fold turnover: later folds trade more."""
    return 0.1 * (fold.fold_index + 1)


def _dispersion(**overrides: object) -> OosDispersionReport:
    """Compute the fold dispersion with overridable arguments."""
    fields: dict[str, object] = {
        "oos_run": _oos_run(),
        "performance_report": _performance_report(),
        "turnover_for": _turnover,
    }
    fields.update(overrides)
    return compute_fold_dispersion(**fields)  # type: ignore[arg-type]


class OosDispersionErrorTests(unittest.TestCase):
    """The dedicated error type and input-consistency guards."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(OosDispersionError, ValueError))

    def test_fold_count_mismatch_rejected(self) -> None:
        # a 3-fold path against the 4-fold run is inconsistent.
        values = (
            NetValue(
                as_of_date=date(2023, 1, 1),
                currency=Currency.HKD,
                cash=1_000_000.0,
                securities_value=0.0,
            ),
            NetValue(
                as_of_date=date(2023, 1, 2),
                currency=Currency.HKD,
                cash=1_050_000.0,
                securities_value=0.0,
            ),
            NetValue(
                as_of_date=date(2023, 1, 3),
                currency=Currency.HKD,
                cash=1_020_000.0,
                securities_value=0.0,
            ),
        )
        tiny = OosEquityPath(
            net_values=values,
            currency=Currency.HKD,
            fold_ranges=((0, 0), (1, 1), (2, 2)),
            dataset_fingerprint=_FINGERPRINT,
            code_version="1.0.0",
            fingerprint="abc",
        )
        report = _performance_report(path=tiny)
        with self.assertRaises(OosDispersionError):
            compute_fold_dispersion(_oos_run(), report, turnover_for=_turnover)


class DispersionPerFoldTests(unittest.TestCase):
    """Each fold's returns, drawdown, turnover, coverage and failure output."""

    def test_each_fold_cumulative_return(self) -> None:
        report = _dispersion()
        expected = (0.364, 0.546, 0.728, 0.91)
        self.assertEqual(len(report), 4)
        for index, fold in enumerate(report):
            self.assertIsNotNone(fold.cumulative_return)
            self.assertAlmostEqual(fold.cumulative_return or 0.0, expected[index], places=6)

    def test_each_fold_max_drawdown(self) -> None:
        report = _dispersion()
        for fold in report:
            self.assertIsNotNone(fold.max_drawdown)
            self.assertGreaterEqual(fold.max_drawdown or 0.0, 0.0)

    def test_each_fold_turnover(self) -> None:
        report = _dispersion()
        for index, fold in enumerate(report):
            self.assertEqual(fold.turnover, 0.1 * (index + 1))

    def test_each_fold_coverage_score(self) -> None:
        report = _dispersion()
        for fold in report:
            self.assertEqual(fold.coverage_pct, 100.0)

    def test_executed_folds_have_no_failure_reason(self) -> None:
        report = _dispersion()
        for fold in report:
            self.assertIsNone(fold.failure_reason)

    def test_readable(self) -> None:
        report = _dispersion()
        self.assertIn("fold 0 return", report[0].readable())
        self.assertIn("coverage", report[0].readable())


class DispersionSummaryTests(unittest.TestCase):
    """Averages do not mask unstable folds — spread and worst fold surface."""

    def test_average_return(self) -> None:
        report = _dispersion()
        self.assertAlmostEqual(report.average_return or 0.0, 0.637, places=6)

    def test_return_spread(self) -> None:
        report = _dispersion()
        self.assertAlmostEqual(report.return_spread or 0.0, 0.546, places=6)

    def test_worst_fold_surfaced(self) -> None:
        report = _dispersion()
        self.assertEqual(report.worst_fold_index, 0)

    def test_cumulative_returns_property(self) -> None:
        report = _dispersion()
        self.assertEqual(len(report.cumulative_returns), 4)

    def test_failure_distribution_empty_when_all_executed(self) -> None:
        report = _dispersion()
        self.assertEqual(report.failure_distribution, ())

    def test_failure_distribution_counts_reasons(self) -> None:
        report = OosDispersionReport(
            folds=(
                FoldDispersion(
                    fold_index=0,
                    cumulative_return=0.364,
                    max_drawdown=0.0,
                    turnover=0.1,
                    coverage_pct=100.0,
                    failure_reason=None,
                ),
                FoldDispersion(
                    fold_index=1,
                    cumulative_return=None,
                    max_drawdown=None,
                    turnover=None,
                    coverage_pct=100.0,
                    failure_reason="no selected candidate",
                ),
                FoldDispersion(
                    fold_index=2,
                    cumulative_return=None,
                    max_drawdown=None,
                    turnover=None,
                    coverage_pct=95.0,
                    failure_reason="no selected candidate",
                ),
            ),
            dataset_fingerprint=_FINGERPRINT,
            code_version="1.0.0",
            fingerprint="abc",
        )
        self.assertEqual(
            report.failure_distribution,
            (("no selected candidate", 2),),
        )

    def test_readable_reports_spread_and_worst(self) -> None:
        report = _dispersion()
        self.assertIn("avg return", report.readable())
        self.assertIn("spread", report.readable())
        self.assertIn("worst fold 0", report.readable())
        self.assertIn("failures 0", report.readable())


class FoldDispersionValidationTests(unittest.TestCase):
    """The per-fold record enforces executed / not-executed invariants."""

    def test_executed_with_failure_reason_rejected(self) -> None:
        with self.assertRaises(OosDispersionError):
            FoldDispersion(
                fold_index=0,
                cumulative_return=0.364,
                max_drawdown=0.0,
                turnover=0.1,
                coverage_pct=100.0,
                failure_reason="boom",
            )

    def test_not_executed_without_failure_reason_rejected(self) -> None:
        with self.assertRaises(OosDispersionError):
            FoldDispersion(
                fold_index=0,
                cumulative_return=None,
                max_drawdown=None,
                turnover=None,
                coverage_pct=100.0,
                failure_reason=None,
            )

    def test_negative_fold_index_rejected(self) -> None:
        with self.assertRaises(OosDispersionError):
            FoldDispersion(
                fold_index=-1,
                cumulative_return=0.1,
                max_drawdown=0.0,
                turnover=None,
                coverage_pct=100.0,
                failure_reason=None,
            )


class OosDispersionReportValidationTests(unittest.TestCase):
    """The report rejects an inconsistent, un-auditable record."""

    def test_empty_folds_rejected(self) -> None:
        with self.assertRaises(OosDispersionError):
            replace(_dispersion(), folds=())

    def test_non_sequential_rejected(self) -> None:
        report = _dispersion()
        second = replace(report[1], fold_index=2)
        with self.assertRaises(OosDispersionError):
            replace(report, folds=(report[0], second))

    def test_empty_fingerprint_rejected(self) -> None:
        with self.assertRaises(OosDispersionError):
            replace(_dispersion(), fingerprint="")

    def test_len_iter_getitem(self) -> None:
        report = _dispersion()
        self.assertEqual(len(report), len(list(report)))
        self.assertEqual(list(report)[2].fold_index, report[2].fold_index)
        with self.assertRaises(IndexError):
            report[99]


class FingerprintTests(unittest.TestCase):
    """The dispersion report fingerprint is stable and re-derivable."""

    def test_fingerprint_is_sha256_hex(self) -> None:
        self.assertRegex(_dispersion().fingerprint, r"^[0-9a-f]{64}$")

    def test_fingerprint_rederivable(self) -> None:
        report = _dispersion()
        self.assertEqual(report.fingerprint, oos_dispersion_fingerprint(report))

    def test_fingerprint_stable_across_equal_reports(self) -> None:
        self.assertEqual(_dispersion().fingerprint, _dispersion().fingerprint)

    def test_fingerprint_changes_with_path(self) -> None:
        self.assertNotEqual(
            _dispersion(
                performance_report=_performance_report(path=_varied_path(rate=0.002))
            ).fingerprint,
            _dispersion().fingerprint,
        )

    def test_fingerprint_changes_with_turnover(self) -> None:
        self.assertNotEqual(
            _dispersion(turnover_for=lambda fold: 0.9).fingerprint,
            _dispersion().fingerprint,
        )

    def test_json_excludes_derived_fingerprint(self) -> None:
        payload = json.loads(oos_dispersion_json(_dispersion()))
        self.assertNotIn("fingerprint", payload)
        self.assertIn("folds", payload)
        self.assertEqual(len(payload["folds"]), 4)

    def test_json_is_key_sorted(self) -> None:
        payload = json.loads(oos_dispersion_json(_dispersion()))
        self.assertEqual(
            list(payload.keys()),
            ["code_version", "dataset_fingerprint", "folds"],
        )
        first = payload["folds"][0]
        self.assertEqual(
            list(first.keys()),
            [
                "coverage_pct",
                "cumulative_return",
                "failure_reason",
                "fold_index",
                "max_drawdown",
                "turnover",
            ],
        )
        self.assertEqual(first["fold_index"], 0)


if __name__ == "__main__":
    unittest.main()
