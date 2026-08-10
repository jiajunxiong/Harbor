"""OOS replayability tests (MVP 3 / SP 3.46, TEST-ONLY).

With the same manifest, split, parameter selection, calendar and seed executed
twice, the fold runs, net-value concatenation and metrics are identical
(相同清单、切分、参数选择、日历和种子重复执行，折叠运行、净值拼接和指标一致).

A single :func:`_run` executes the full out-of-sample pipeline — SP 3.35 fold
runs, SP 3.36 chain integrity, SP 3.37 concatenation, SP 3.38 performance and
risk metrics, SP 3.39 fold dispersion and SP 3.40 cross-market reconciliation —
over fixed inputs and captures every output plus its derived fingerprint into
a frozen :class:`ReplayResult`. Running it twice yields byte-identical outputs
(the acceptance) and every derived fingerprint is re-derivable; negative
controls (different dataset fingerprint, selection, calendar or seed) prove the
determinism is anchored to the inputs rather than vacuous.
"""

import unittest
from dataclasses import dataclass, replace
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
from harbor.core.market_registry import CorporateActionType
from harbor.core.oos_chain import OosChainIntegrity, oos_chain_fingerprint, verify_fold_chains
from harbor.core.oos_concat import OosEquityPath, concatenate_fold_oos, oos_concat_fingerprint
from harbor.core.oos_dispersion import (
    OosDispersionReport,
    compute_fold_dispersion,
    oos_dispersion_fingerprint,
)
from harbor.core.oos_performance import (
    OosPerformanceReport,
    compute_oos_metrics,
    oos_metrics_fingerprint,
)
from harbor.core.oos_reconcile import (
    CrossMarketOosReconcile,
    oos_reconcile_fingerprint,
    reconcile_cross_market_oos,
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
from harbor.core.rolling_oos import (
    OosRunOutcome,
    RollingOosRun,
    rolling_oos_fingerprint,
    run_rolling_oos,
)
from harbor.core.rolling_train import RollingTrainRun, run_rolling_training
from harbor.core.rolling_validate import (
    ValidationComponents,
    run_rolling_validation,
)
from harbor.core.rolling_window import FoldSequence, build_walk_forward_folds
from harbor.core.test_access_guard import AccessGuard
from harbor.core.trade_metrics import TradeStats
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
_CREATED = datetime(2025, 1, 1, tzinfo=timezone.utc)
_BASE_HOLIDAYS = {
    Market.HK: frozenset({date(2023, 1, 2)}),
    Market.US: frozenset(),
}


@dataclass(frozen=True)
class ReplayResult:
    """Every OOS output captured by one pipeline run (SP 3.46)."""

    oos_run: RollingOosRun
    oos_fingerprint: str
    chains: OosChainIntegrity
    chain_fingerprint: str
    path: OosEquityPath
    concat_fingerprint: str
    performance: OosPerformanceReport
    performance_fingerprint: str
    dispersion: OosDispersionReport
    dispersion_fingerprint: str
    reconcile: CrossMarketOosReconcile
    reconcile_fingerprint: str


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


def _budget(seed: int = 42) -> TrialBudget:
    return TrialBudget(max_trials=3, random_seed=seed)


def _tuning(seed: int = 42) -> TuningConfig:
    return TuningConfig(
        primary_metric="sharpe",
        metric_direction=MetricDirection.HIGHER_BETTER,
        max_trials=3,
        random_seed=seed,
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


def _make_fit_factory(fp: str):
    """A training fit bound to the dataset fingerprint ``fp``."""

    def fit_factory(train_start: date, train_end: date):
        return build_training_fit(
            fit_start=train_start,
            fit_end=train_end,
            dataset_fingerprint=fp,
            code_version="1.0.0",
            fitted_state=(("lookback", 252.0),),
        )

    return fit_factory


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


def _registration(**overrides: object):
    """Register the independent holdout over the base split."""
    fields: dict[str, object] = {
        "test_set_id": "holdout-1",
        "split": _split(),
        "config_hash": "cfg-hash",
        "created_at": _CREATED,
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


def _report_artifact(fold) -> str:
    """Deterministic report-artifact fingerprint per fold (SP 3.36)."""
    return f"report-{fold.fold_index}"


def _net_values_for(fold) -> tuple[NetValue, ...]:
    """Deterministic per-fold OOS net values (fold-boundary drawdowns)."""
    factor = 1.0 + 0.5 * fold.fold_index
    return tuple(
        NetValue(
            as_of_date=fold.test_start + timedelta(days=index),
            currency=Currency.HKD,
            cash=1_000_000.0 * (1.0 + 0.001 * factor * index),
            securities_value=0.0,
        )
        for index in range((fold.test_end - fold.test_start).days + 1)
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


def _turnover(fold) -> float:
    """Per-fold turnover: later folds trade more."""
    return 0.1 * (fold.fold_index + 1)


def _calendar(holidays=None) -> MarketTradingCalendar:
    """A fixed market calendar (overridable holiday sets)."""
    return MarketTradingCalendar(holidays=holidays if holidays is not None else _BASE_HOLIDAYS)


def _fx_rate(quote: Currency, base: Currency, day: date) -> float | None:
    """Positive FX whenever the currencies differ."""
    if quote is base:
        return 1.0
    return 7.85


def _trading_days(market: Market, fold) -> tuple[date, ...]:
    """The market's trading days in the fold's OOS interval."""
    return tuple(_calendar().trading_days(market, fold.test_start, fold.test_end))


def _cost_model(market: Market, fold) -> str:
    """The correct cost model per market (SP 2.37 HK / SP 2.38 US)."""
    return "hk" if market is Market.HK else "us"


def _corporate_actions(market: Market, fold) -> tuple[CorporateActionType, ...]:
    """The corporate actions processed: each market's own allowed types."""
    if market is Market.HK:
        return (CorporateActionType.DIVIDEND, CorporateActionType.RIGHTS_ISSUE)
    return (CorporateActionType.DIVIDEND, CorporateActionType.SPLIT)


def _training_run(
    *,
    dataset_fingerprint: str,
    candidates: tuple[dict[str, object], ...],
    seed: int,
) -> RollingTrainRun:
    """Run the SP 3.33 rolling training with fixed inputs."""
    return run_rolling_training(
        sequence=_sequence(dataset_fingerprint=dataset_fingerprint),
        space=_space(),
        budget=_budget(seed),
        market=Market.HK,
        dataset_fingerprint=dataset_fingerprint,
        code_version="1.0.0",
        tuning=_tuning(seed),
        candidate_parameter_sets=candidates,
        fit_factory=_make_fit_factory(dataset_fingerprint),
        evaluate=_evaluate,
        constraints=(),
        validation_samples=200,
    )


def _run(
    *,
    dataset_fingerprint: str = _FINGERPRINT,
    candidates: tuple[dict[str, object], ...] | None = None,
    seed: int = 42,
    holidays=None,
) -> ReplayResult:
    """Execute the full OOS pipeline once over fixed inputs (SP 3.46)."""
    fp = dataset_fingerprint
    candidate_set = candidates if candidates is not None else tuple(_candidates())
    training = _training_run(dataset_fingerprint=fp, candidates=candidate_set, seed=seed)
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
        requested_at=_AT,
    )
    chains = verify_fold_chains(oos, report_artifact_for=_report_artifact)
    path = concatenate_fold_oos(oos, net_values_for=_net_values_for)
    performance = compute_oos_metrics(
        path,
        trade_stats_for=_trade_stats,
        exposure_for=_exposure,
        benchmark_return_for=_benchmark_return,
    )
    dispersion = compute_fold_dispersion(oos, performance, turnover_for=_turnover)
    reconcile = reconcile_cross_market_oos(
        {Market.HK: oos},
        base_currency=Currency.HKD,
        calendar=_calendar(holidays),
        calendar_version="cal-1",
        fx_rate_for=_fx_rate,
        trading_days_for=_trading_days,
        cost_model_for=_cost_model,
        corporate_actions_for=_corporate_actions,
    )
    return ReplayResult(
        oos_run=oos,
        oos_fingerprint=rolling_oos_fingerprint(oos),
        chains=chains,
        chain_fingerprint=oos_chain_fingerprint(chains),
        path=path,
        concat_fingerprint=oos_concat_fingerprint(path),
        performance=performance,
        performance_fingerprint=oos_metrics_fingerprint(performance),
        dispersion=dispersion,
        dispersion_fingerprint=oos_dispersion_fingerprint(dispersion),
        reconcile=reconcile,
        reconcile_fingerprint=oos_reconcile_fingerprint(reconcile),
    )


class ReplayDeterminismTests(unittest.TestCase):
    """Two runs over the same fixed inputs produce identical outputs."""

    def test_identical_oos_run(self) -> None:
        first, second = _run(), _run()
        self.assertEqual(first.oos_run, second.oos_run)
        self.assertEqual(
            [result.fold for result in first.oos_run],
            [result.fold for result in second.oos_run],
        )

    def test_identical_chain_integrity(self) -> None:
        self.assertEqual(_run().chains, _run().chains)

    def test_identical_path(self) -> None:
        first, second = _run(), _run()
        self.assertEqual(first.path, second.path)
        self.assertEqual(first.path.net_values, second.path.net_values)
        self.assertEqual(first.path.fold_ranges, second.path.fold_ranges)

    def test_identical_performance(self) -> None:
        self.assertEqual(_run().performance, _run().performance)

    def test_identical_dispersion(self) -> None:
        self.assertEqual(_run().dispersion, _run().dispersion)

    def test_identical_reconcile(self) -> None:
        self.assertEqual(_run().reconcile, _run().reconcile)

    def test_full_replay(self) -> None:
        # the acceptance: identical manifest/split/selection/calendar/seed run
        # twice ⇒ fold runs, concatenation and metrics are identical.
        self.assertEqual(_run(), _run())

    def test_fingerprints_identical_across_runs(self) -> None:
        first, second = _run(), _run()
        for attribute in (
            "oos_fingerprint",
            "chain_fingerprint",
            "concat_fingerprint",
            "performance_fingerprint",
            "dispersion_fingerprint",
            "reconcile_fingerprint",
        ):
            self.assertEqual(getattr(first, attribute), getattr(second, attribute))


class FingerprintStabilityTests(unittest.TestCase):
    """Every derived fingerprint is re-derivable and stable."""

    def test_oos_fingerprint_rederivable(self) -> None:
        result = _run()
        self.assertEqual(result.oos_fingerprint, rolling_oos_fingerprint(result.oos_run))

    def test_chain_fingerprint_rederivable(self) -> None:
        result = _run()
        self.assertEqual(result.chain_fingerprint, oos_chain_fingerprint(result.chains))

    def test_concat_fingerprint_rederivable(self) -> None:
        result = _run()
        self.assertEqual(result.concat_fingerprint, oos_concat_fingerprint(result.path))

    def test_metrics_fingerprint_rederivable(self) -> None:
        result = _run()
        self.assertEqual(
            result.performance_fingerprint,
            oos_metrics_fingerprint(result.performance),
        )

    def test_dispersion_fingerprint_rederivable(self) -> None:
        result = _run()
        self.assertEqual(
            result.dispersion_fingerprint,
            oos_dispersion_fingerprint(result.dispersion),
        )

    def test_reconcile_fingerprint_rederivable(self) -> None:
        result = _run()
        self.assertEqual(
            result.reconcile_fingerprint,
            oos_reconcile_fingerprint(result.reconcile),
        )


class FixedInputInvarianceTests(unittest.TestCase):
    """Changing an input changes the anchored outputs (determinism is not vacuous)."""

    def test_different_dataset_fingerprint_changes_outputs(self) -> None:
        first = _run()
        second = _run(dataset_fingerprint="d" * 64)
        self.assertNotEqual(first.oos_fingerprint, second.oos_fingerprint)
        self.assertNotEqual(first.path, second.path)
        self.assertNotEqual(first.performance, second.performance)
        self.assertNotEqual(first.reconcile, second.reconcile)

    def test_different_selection_changes_chain(self) -> None:
        flat = (
            {"cash_weight": 0.05, "factor_weight": 0.95, "lookback": 252},
            {"cash_weight": 0.10, "factor_weight": 0.90, "lookback": 252},
        )
        first = _run()
        second = _run(candidates=flat)
        # the selected trial id changes (tie-broken first candidate), so the
        # evidence chain differs even though the fold runs are identical.
        self.assertNotEqual(first.chain_fingerprint, second.chain_fingerprint)
        self.assertEqual(first.oos_fingerprint, second.oos_fingerprint)

    def test_different_calendar_changes_reconcile(self) -> None:
        extra_holidays = {
            Market.HK: frozenset({date(2023, 1, 2), date(2023, 1, 3)}),
            Market.US: frozenset(),
        }
        first = _run()
        second = _run(holidays=extra_holidays)
        self.assertNotEqual(first.reconcile_fingerprint, second.reconcile_fingerprint)
        self.assertEqual(first.oos_fingerprint, second.oos_fingerprint)

    def test_different_seed_changes_chain(self) -> None:
        first = _run()
        second = _run(seed=7)
        # the seed is part of the trial identity, so the recorded trial
        # fingerprint (and hence the chain) differs; the fold runs do not.
        self.assertNotEqual(first.chain_fingerprint, second.chain_fingerprint)
        self.assertEqual(first.oos_fingerprint, second.oos_fingerprint)
