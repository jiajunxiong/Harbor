"""OOS performance and risk metrics tests (MVP 3 / SP 3.38).

Verifies the concatenated OOS path produces the full metric set — returns,
volatility, drawdown, Sharpe, Calmar (收益/波动/回撤/Sharpe/Calmar), turnover
and costs (换手/成本), exposure (暴露) and benchmark excess performance
(基准超额表现) — and that the report re-verifies the excess return is exactly
portfolio minus benchmark.
"""

import json
import math
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
from harbor.core.oos_performance import (
    OosPerformanceError,
    OosPerformanceReport,
    compute_oos_metrics,
    oos_metrics_fingerprint,
    oos_metrics_json,
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
    """Return a tight pre-registered split with overridable fields.

    The test horizon ends 2026-12-30 (not a multiple of the 365-day step) so
    the final fold is a full-length segment, keeping a positive cumulative
    return on the concatenated growth path.
    """
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


def _growth_series(fold, rate: float = 0.001) -> tuple[NetValue, ...]:
    """A fold's OOS net values with a positive drift (computable metrics)."""
    days = (fold.test_end - fold.test_start).days + 1
    return tuple(
        NetValue(
            as_of_date=fold.test_start + timedelta(days=index),
            currency=Currency.HKD,
            cash=1_000_000.0 * (1.0 + rate * index),
            securities_value=0.0,
        )
        for index in range(days)
    )


def _growth_path(rate: float = 0.001) -> OosEquityPath:
    """Concatenate the OOS run using the growth net-value series."""
    return concatenate_fold_oos(
        _oos_run(),
        net_values_for=lambda fold: _growth_series(fold, rate=rate),
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


def _report(**overrides: object) -> OosPerformanceReport:
    """Compute the OOS metrics report with overridable arguments."""
    fields: dict[str, object] = {
        "path": _growth_path(),
        "trade_stats_for": _trade_stats,
        "exposure_for": _exposure,
        "benchmark_return_for": _benchmark_return,
    }
    fields.update(overrides)
    return compute_oos_metrics(**fields)  # type: ignore[arg-type]


class OosPerformanceErrorTests(unittest.TestCase):
    """The dedicated error type."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(OosPerformanceError, ValueError))

    def test_non_finite_benchmark_return_rejected(self) -> None:
        with self.assertRaises(OosPerformanceError):
            _report(benchmark_return_for=lambda path: math.inf)


class MetricComputationTests(unittest.TestCase):
    """The full metric set is computed on the concatenated result (SP 3.38)."""

    def test_return_and_risk_metrics_present(self) -> None:
        report = _report()
        performance = report.performance
        self.assertGreater(performance.cumulative_return, 0.0)
        self.assertGreater(performance.annualized_volatility, 0.0)
        # MVP 2 max_drawdown is a non-negative fraction; the fold-boundary
        # resets produce a real peak-to-trough decline.
        self.assertGreater(performance.max_drawdown, 0.0)
        self.assertTrue(math.isfinite(performance.sharpe_ratio))
        self.assertTrue(math.isfinite(performance.calmar_ratio))
        self.assertGreaterEqual(performance.periods, 3)

    def test_turnover_and_costs_recorded(self) -> None:
        report = _report()
        self.assertEqual(report.trade_stats.turnover, 0.4)
        self.assertEqual(report.trade_stats.total_fees_base, 120.0)
        self.assertEqual(report.trade_stats.slippage_cost_base, 40.0)
        self.assertEqual(report.trade_stats.fill_count, 4)

    def test_exposure_recorded(self) -> None:
        report = _report()
        self.assertEqual(len(report.exposure.points), 2)
        self.assertEqual(report.exposure.points[0].cash_exposure, 0.5)

    def test_excess_return_is_portfolio_minus_benchmark(self) -> None:
        report = _report()
        expected = report.performance.cumulative_return - 0.02
        self.assertAlmostEqual(report.excess_return, expected, places=9)

    def test_report_inherits_path_context(self) -> None:
        report = _report()
        self.assertEqual(report.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(report.code_version, "1.0.0")

    def test_report_iterates_the_path(self) -> None:
        report = _report()
        self.assertEqual(len(report), len(report.path))
        self.assertEqual(list(report)[0].as_of_date, report.path.start_date)


class ReportValidationTests(unittest.TestCase):
    """The report rejects an inconsistent, un-auditable record."""

    def test_inconsistent_excess_return_rejected(self) -> None:
        report = _report()
        with self.assertRaises(OosPerformanceError):
            replace(report, excess_return=0.99)

    def test_dataset_fingerprint_mismatch_rejected(self) -> None:
        report = _report()
        with self.assertRaises(OosPerformanceError):
            replace(report, dataset_fingerprint="g" * 64)

    def test_empty_fingerprint_rejected(self) -> None:
        report = _report()
        with self.assertRaises(OosPerformanceError):
            replace(report, fingerprint="")

    def test_readable(self) -> None:
        report = _report()
        self.assertIn("OOS performance", report.readable())
        self.assertIn("excess", report.readable())


class FingerprintTests(unittest.TestCase):
    """The report fingerprint is stable and re-derivable."""

    def test_fingerprint_is_sha256_hex(self) -> None:
        self.assertRegex(_report().fingerprint, r"^[0-9a-f]{64}$")

    def test_fingerprint_rederivable(self) -> None:
        report = _report()
        self.assertEqual(report.fingerprint, oos_metrics_fingerprint(report))

    def test_fingerprint_stable_across_equal_reports(self) -> None:
        self.assertEqual(_report().fingerprint, _report().fingerprint)

    def test_fingerprint_changes_with_performance(self) -> None:
        self.assertNotEqual(
            _report(path=_growth_path(rate=0.002)).fingerprint,
            _report().fingerprint,
        )

    def test_fingerprint_changes_with_trade_stats(self) -> None:
        def alt_trade(path: OosEquityPath) -> TradeStats:
            return replace(_trade_stats(path), turnover=0.7)

        self.assertNotEqual(
            _report(trade_stats_for=alt_trade).fingerprint,
            _report().fingerprint,
        )

    def test_fingerprint_changes_with_benchmark(self) -> None:
        self.assertNotEqual(
            _report(benchmark_return_for=lambda path: 0.05).fingerprint,
            _report().fingerprint,
        )

    def test_fingerprint_changes_with_exposure(self) -> None:
        def alt_exposure(path: OosEquityPath) -> ExposureSeries:
            return ExposureSeries(
                points=(
                    _exposure_point(path.start_date, cash_exposure=0.6),
                    _exposure_point(path.end_date, cash_exposure=0.6),
                )
            )

        self.assertNotEqual(
            _report(exposure_for=alt_exposure).fingerprint,
            _report().fingerprint,
        )

    def test_json_excludes_derived_fingerprint(self) -> None:
        payload = json.loads(oos_metrics_json(_report()))
        self.assertNotIn("fingerprint", payload)
        for key in ("performance", "trade", "exposure", "benchmark_return", "excess_return"):
            self.assertIn(key, payload)

    def test_json_is_key_sorted(self) -> None:
        payload = json.loads(oos_metrics_json(_report()))
        self.assertEqual(
            list(payload.keys()),
            [
                "benchmark_return",
                "code_version",
                "dataset_fingerprint",
                "excess_return",
                "exposure",
                "performance",
                "trade",
            ],
        )
        self.assertIn("cumulative_return", payload["performance"])
        self.assertIn("turnover", payload["trade"])
        self.assertIn("average_cash_exposure", payload["exposure"])


if __name__ == "__main__":
    unittest.main()
