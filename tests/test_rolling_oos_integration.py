"""Rolling OOS integration tests (MVP 3 / SP 3.78).

Runs the FULL multi-fold rolling OOS pipeline on fixed HK, US and cross-market
Mock data (固定 HK、US 和跨市场 Mock 数据跑通多折叠) and verifies the four
acceptance dimensions — 多折叠 (multi-fold, SP 3.31–3.35), OOS 拼接 (OOS
concatenation, SP 3.37), 账本对账 (ledger/OOS reconciliation, SP 3.23/3.40) and
结果查询 (result query, SP 3.38/3.39/3.36) — end to end.

For each market the pipeline runs: rolling training (SP 3.33) → rolling
validation (SP 3.34) → rolling OOS execution (SP 3.35) → OOS concatenation
(SP 3.37) → OOS performance metrics (SP 3.38) → fold dispersion (SP 3.39) →
evidence chains (SP 3.36). The cross-market case additionally reconciles FX /
calendar / cost / corporate-action handling (SP 3.40) and requires a positive
FX rate (缺失 FX 拒绝计算). The per-fold OOS net values are ledger-reconciled
(SP 3.23 ``net_values_reconcile``). No database is required.
"""

import unittest
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType

from harbor.core.backtest_config import BenchmarkKind
from harbor.core.backtest_domain import Currency, Market, NetValue
from harbor.core.benchmark import BenchmarkLevel, BenchmarkSeries
from harbor.core.coverage_scoring import CoverageMeasurement, CoverageScore, MarketCoverage
from harbor.core.drawdown_events import DrawdownConfig, DrawdownSeries
from harbor.core.exposure import ExposurePoint, ExposureSeries
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.holdout_registry import register_test_set
from harbor.core.market_registry import CorporateActionType
from harbor.core.oos_chain import OosChainIntegrity, oos_chain_fingerprint, verify_fold_chains
from harbor.core.oos_concat import OosEquityPath, concatenate_fold_oos
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
    MissingFxError,
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
from harbor.core.rolling_oos import OosRunOutcome, RollingOosRun, run_rolling_oos
from harbor.core.rolling_train import RollingTrainRun, run_rolling_training
from harbor.core.rolling_validate import (
    RollingValidationRun,
    ValidationComponents,
    run_rolling_validation,
)
from harbor.core.rolling_window import FoldSequence, build_walk_forward_folds
from harbor.core.test_access_guard import AccessGuard
from harbor.core.trade_metrics import TradeStats
from harbor.core.trading_calendar import MarketTradingCalendar
from harbor.core.training_fit import build_training_fit
from harbor.core.trial_budget import TrialBudget
from harbor.core.trial_reconciliation import net_values_reconcile
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
_CODE_VERSION = "1.0.0"
_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _each_day(start: date, end: date) -> tuple[date, ...]:
    """Return every calendar day in the inclusive range."""
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return tuple(days)


class _MockMarketData:
    """Fixed deterministic market data (prices / fundamentals)."""

    def __init__(self, price_base: float = 50.0, rate: float = 0.001) -> None:
        self.price_base = price_base
        self.rate = rate

    def price(self, day: date) -> float:
        return self.price_base * (1.0 + self.rate * day.toordinal())

    def prices(self, start: date, end: date) -> dict[date, dict[str, float]]:
        return {
            day: {"AAA": self.price(day), "BBB": self.price(day) * 2.0}
            for day in _each_day(start, end)
        }

    def fundamentals(self, start: date, end: date) -> dict[date, dict[str, float]]:
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
    """Return an expanding, every-fold rolling config (4 folds)."""
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
    """Return a three-parameter space applying to HK and US."""
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
    """A training fit that reads prices over the training window."""

    def fit_factory(train_start: date, train_end: date):
        prices = data.prices(train_start, train_end)
        average = sum(day["AAA"] for day in prices.values()) / max(1, len(prices))
        return build_training_fit(
            fit_start=train_start,
            fit_end=train_end,
            dataset_fingerprint=_FINGERPRINT,
            code_version=_CODE_VERSION,
            fitted_state=(("lookback", average),),
        )

    return fit_factory


def _make_application_factory(data: _MockMarketData):
    """A validation application reading fundamentals over the validation window."""

    def application_factory(fold_result, decision_date: date) -> ValidationApplication:
        fold = fold_result.fold
        data.fundamentals(fold.validation_start, decision_date)
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


def _make_compute_validation(data: _MockMarketData, *, market: Market):
    """The four validation results, reading prices over the validation window."""

    def compute_validation(fold_result, application) -> ValidationComponents:
        fold = fold_result.fold
        data.prices(fold.validation_start, fold.validation_end)
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
            market=market,
            scores=(
                CoverageScore(
                    market=market,
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
        code_version=_CODE_VERSION,
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
    """The MVP 2 engine reading prices over the fold's OOS interval."""

    def run_engine(fold, selected) -> OosRunOutcome:
        data.prices(fold.test_start, fold.test_end)
        run_id = f"oos-run-{fold.fold_index}"
        return OosRunOutcome(run_id=run_id, replay_manifest=_manifest(fold, run_id))

    return run_engine


def _make_net_values(data: _MockMarketData, *, currency: Currency):
    """Per-fold OOS net values (varying, ledger-consistent), reading prices."""

    def net_values_for(fold) -> tuple[NetValue, ...]:
        days = tuple(data.prices(fold.test_start, fold.test_end))
        factor = 1.0 + 0.5 * fold.fold_index
        values = []
        for index, day in enumerate(days):
            securities = 1_000_000.0 * 0.5 * (1.0 + 0.001 * factor * index)
            cash = 1_000_000.0 * 0.5 * (1.0 + 0.001 * factor * index)
            values.append(
                NetValue(
                    as_of_date=day,
                    currency=currency,
                    cash=cash,
                    securities_value=securities,
                )
            )
        return tuple(values)

    return net_values_for


def _trade_stats(path: OosEquityPath) -> TradeStats:
    """Fixed trade/turnover stats over the OOS path."""
    return TradeStats(
        fill_count=4,
        buy_count=2,
        sell_count=2,
        round_trip_count=2,
        win_count=1,
        win_rate=0.5,
        average_holding_days=20.0,
        turnover=0.4,
        total_fees_base=120.0,
        slippage_cost_base=40.0,
        unfilled_count=0,
        refused_reasons=MappingProxyType({}),
    )


def _exposure(path: OosEquityPath) -> ExposureSeries:
    """A single exposure point over the OOS path."""
    point = ExposurePoint(
        as_of=path.start_date,
        base_currency=path.currency,
        total_value=1_000_000.0,
        cash_exposure=0.5,
        market_exposure=MappingProxyType({}),
        currency_exposure=MappingProxyType({path.currency: 1.0}),
        symbol_exposure=MappingProxyType({}),
        industry_exposure=None,
    )
    return ExposureSeries(points=(point,))


@dataclass(frozen=True)
class _PipelineResult:
    """Every output of the full rolling OOS pipeline for one market."""

    market: Market
    base_currency: Currency
    training_run: RollingTrainRun
    validation_run: RollingValidationRun
    oos_run: RollingOosRun
    path: OosEquityPath
    metrics: OosPerformanceReport
    dispersion: OosDispersionReport
    chains: OosChainIntegrity


def _run_training(data: _MockMarketData, *, market: Market) -> RollingTrainRun:
    """Run the SP 3.33 rolling training for one market."""
    return run_rolling_training(
        sequence=_sequence(),
        space=_space(),
        budget=_budget(),
        market=market,
        dataset_fingerprint=_FINGERPRINT,
        code_version=_CODE_VERSION,
        tuning=_tuning(),
        candidate_parameter_sets=_candidates(),
        fit_factory=_make_fit_factory(data),
        evaluate=_evaluate,
        constraints=(),
        validation_samples=200,
    )


def _run_validation(
    training_run: RollingTrainRun, data: _MockMarketData, *, market: Market
) -> RollingValidationRun:
    """Run the SP 3.34 rolling validation for one market."""
    return run_rolling_validation(
        training_run,
        application_factory=_make_application_factory(data),
        compute_validation=_make_compute_validation(data, market=market),
    )


def _run_oos(validation_run: RollingValidationRun, data: _MockMarketData) -> RollingOosRun:
    """Run the SP 3.35 rolling OOS execution."""
    return run_rolling_oos(
        validation_run,
        guard=_guard(),
        current_stage=ValidationStatus.TEST_LOCKED,
        run_engine=_make_run_engine(data),
        requested_at=_AT,
    )


def _run_pipeline(
    data: _MockMarketData, *, market: Market, base_currency: Currency
) -> _PipelineResult:
    """Run the full pipeline: train → validate → OOS → concat → metrics → dispersion → chains."""
    training = _run_training(data, market=market)
    validation = _run_validation(training, data, market=market)
    oos = _run_oos(validation, data)
    path = concatenate_fold_oos(oos, net_values_for=_make_net_values(data, currency=base_currency))
    metrics = compute_oos_metrics(
        path,
        trade_stats_for=_trade_stats,
        exposure_for=_exposure,
        benchmark_return_for=lambda _path: 0.02,
    )
    dispersion = compute_fold_dispersion(oos, metrics, turnover_for=lambda _path: 0.4)
    chains = verify_fold_chains(oos, report_artifact_for=lambda fold: f"report-{fold.fold_index}")
    return _PipelineResult(
        market=market,
        base_currency=base_currency,
        training_run=training,
        validation_run=validation,
        oos_run=oos,
        path=path,
        metrics=metrics,
        dispersion=dispersion,
        chains=chains,
    )


def _calendar() -> MarketTradingCalendar:
    """An authoritative calendar with no holidays (weekday trading)."""
    return MarketTradingCalendar(holidays={Market.HK: frozenset(), Market.US: frozenset()})


def _trading_days_for(market: Market, fold) -> tuple[date, ...]:
    """The engine's actual OOS trading days (weekdays in the fold's interval)."""
    return tuple(day for day in _each_day(fold.test_start, fold.test_end) if day.weekday() < 5)


def _fx_rate_for(frm: Currency, to: Currency, day: date) -> float | None:
    """Fixed FX: USD → HKD 7.8, otherwise None."""
    if frm is Currency.USD and to is Currency.HKD:
        return 7.8
    return None


def _cost_model_for(market: Market, fold) -> str:
    """The cost model the engine used per market (SP 2.37/2.38)."""
    return "hk" if market is Market.HK else "us"


def _corporate_actions_for(market: Market, fold) -> tuple[CorporateActionType, ...]:
    """The CA types the engine processed per market (SP 2.44)."""
    if market is Market.HK:
        return (CorporateActionType.RIGHTS_ISSUE,)
    return (CorporateActionType.SPLIT,)


def _reconcile(pipelines: dict[Market, _PipelineResult]) -> CrossMarketOosReconcile:
    """Reconcile the cross-market OOS runs (SP 3.40)."""
    runs = {market: pipeline.oos_run for market, pipeline in pipelines.items()}
    return reconcile_cross_market_oos(
        runs,
        base_currency=Currency.HKD,
        calendar=_calendar(),
        calendar_version="cal-1",
        fx_rate_for=_fx_rate_for,
        trading_days_for=_trading_days_for,
        cost_model_for=_cost_model_for,
        corporate_actions_for=_corporate_actions_for,
    )


class MultiFoldPipelineTests(unittest.TestCase):
    """SP 3.31–3.35 多折叠: the pipeline runs multiple folds for each market."""

    def setUp(self) -> None:
        self.data = _MockMarketData()
        self.hk = _run_pipeline(self.data, market=Market.HK, base_currency=Currency.HKD)
        self.us = _run_pipeline(self.data, market=Market.US, base_currency=Currency.HKD)

    def test_hk_pipeline_runs_four_folds(self) -> None:
        self.assertEqual(len(self.hk.training_run), 4)
        self.assertEqual(len(self.hk.validation_run), 4)
        self.assertEqual(len(self.hk.oos_run), 4)
        self.assertEqual(self.hk.path.fold_count, 4)

    def test_us_pipeline_runs_four_folds(self) -> None:
        self.assertEqual(len(self.us.training_run), 4)
        self.assertEqual(len(self.us.validation_run), 4)
        self.assertEqual(len(self.us.oos_run), 4)

    def test_every_fold_executed_with_run_id(self) -> None:
        for result in self.hk.oos_run:
            self.assertTrue(result.executed)
            self.assertEqual(result.run_id, f"oos-run-{result.fold.fold_index}")
            manifest = result.replay_manifest
            self.assertIsNotNone(manifest)
            assert manifest is not None
            self.assertEqual(manifest.data_boundaries.start_date, result.fold.test_start)

    def test_cross_market_both_runs_multi_fold(self) -> None:
        cross = _reconcile({Market.HK: self.hk, Market.US: self.us})
        self.assertEqual(len(cross.market_results), 2)
        self.assertEqual(cross.fold_count, 8)


class OosConcatenationTests(unittest.TestCase):
    """SP 3.37 OOS 拼接: the concatenated path spans the full horizon contiguously."""

    def setUp(self) -> None:
        self.pipeline = _run_pipeline(
            _MockMarketData(), market=Market.HK, base_currency=Currency.HKD
        )
        self.path = self.pipeline.path

    def test_path_spans_full_test_horizon(self) -> None:
        self.assertEqual(self.path.start_date, _split().test_start)
        self.assertEqual(self.path.end_date, _split().test_end)

    def test_path_is_contiguous_day_by_day(self) -> None:
        values = self.path.net_values
        for previous, current in zip(values, values[1:]):
            self.assertEqual(current.as_of_date, previous.as_of_date + timedelta(days=1))

    def test_fold_ranges_slice_each_fold(self) -> None:
        self.assertEqual(self.path.fold_count, 4)
        for fold_index in range(4):
            segment = self.path.fold_net_values(fold_index)
            self.assertGreater(len(segment), 0)
            start, end = self.path.fold_ranges[fold_index]
            self.assertEqual(segment[0].as_of_date, self.path.net_values[start].as_of_date)
            self.assertEqual(segment[-1].as_of_date, self.path.net_values[end].as_of_date)

    def test_path_currency_and_ledger_reconcile(self) -> None:
        self.assertEqual(self.path.currency, Currency.HKD)
        self.assertTrue(net_values_reconcile(self.path.net_values))


class LedgerReconciliationTests(unittest.TestCase):
    """SP 3.40/3.23 账本对账: cross-market OOS reconciliation closes cleanly."""

    def setUp(self) -> None:
        data = _MockMarketData()
        self.hk = _run_pipeline(data, market=Market.HK, base_currency=Currency.HKD)
        self.us = _run_pipeline(data, market=Market.US, base_currency=Currency.HKD)
        self.report = _reconcile({Market.HK: self.hk, Market.US: self.us})

    def test_cross_market_reconcile_passes(self) -> None:
        self.assertEqual(self.report.markets, (Market.HK, Market.US))
        self.assertTrue(self.report.reconciled)
        self.assertEqual(self.report.failures, ())

    def test_hk_needs_no_fx(self) -> None:
        hk = self.report.market_results[0]
        self.assertFalse(hk.fx_required)
        self.assertTrue(hk.reconciled)

    def test_us_requires_fx_and_reconciles(self) -> None:
        us = self.report.market_results[1]
        self.assertTrue(us.fx_required)
        self.assertTrue(us.reconciled)
        fx_check = us.checks[0]
        self.assertTrue(fx_check.reconciled)
        self.assertIn("positive FX", fx_check.detail)

    def test_calendar_days_are_trading_days(self) -> None:
        for market_result in self.report.market_results:
            calendar_check = market_result.checks[1]
            self.assertTrue(calendar_check.reconciled, calendar_check.reason)

    def test_cost_and_corporate_actions_match_markets(self) -> None:
        for market_result in self.report.market_results:
            self.assertTrue(market_result.checks[2].reconciled)  # cost
            self.assertTrue(market_result.checks[3].reconciled)  # corporate actions

    def test_missing_fx_refuses_reconciliation(self) -> None:
        def no_fx(frm: Currency, to: Currency, day: date) -> float | None:
            return None

        with self.assertRaises(MissingFxError):
            reconcile_cross_market_oos(
                {Market.HK: self.hk.oos_run, Market.US: self.us.oos_run},
                base_currency=Currency.HKD,
                calendar=_calendar(),
                fx_rate_for=no_fx,
                trading_days_for=_trading_days_for,
                cost_model_for=_cost_model_for,
                corporate_actions_for=_corporate_actions_for,
            )


class ResultQueryTests(unittest.TestCase):
    """SP 3.38/3.39/3.36 结果查询: per-fold, metrics, dispersion and chains."""

    def setUp(self) -> None:
        self.pipeline = _run_pipeline(
            _MockMarketData(), market=Market.HK, base_currency=Currency.HKD
        )

    def test_query_fold_oos_result(self) -> None:
        result = self.pipeline.oos_run.oos_for(2)
        self.assertEqual(result.run_id, "oos-run-2")
        self.assertEqual(result.fold.fold_index, 2)

    def test_oos_metrics_queryable(self) -> None:
        metrics = self.pipeline.metrics
        self.assertGreater(metrics.performance.cumulative_return, 0.0)
        self.assertEqual(metrics.benchmark_return, 0.02)
        self.assertAlmostEqual(
            metrics.excess_return,
            metrics.performance.cumulative_return - 0.02,
            places=6,
        )
        self.assertEqual(metrics.trade_stats.turnover, 0.4)

    def test_dispersion_queryable(self) -> None:
        dispersion = self.pipeline.dispersion
        self.assertEqual(len(dispersion.folds), 4)
        self.assertGreaterEqual(dispersion.average_return, 0.0)
        self.assertIsNotNone(dispersion.worst_fold_index)
        self.assertEqual(dispersion.failure_distribution, ())

    def test_chain_integrity_complete(self) -> None:
        chains = self.pipeline.chains
        self.assertTrue(chains.complete)
        self.assertEqual(chains.incomplete_chains, ())
        self.assertEqual(chains.complete_chains_count(), 4)

    def test_fingerprints_rederivable(self) -> None:
        self.assertEqual(
            self.pipeline.metrics.fingerprint, oos_metrics_fingerprint(self.pipeline.metrics)
        )
        self.assertEqual(
            self.pipeline.dispersion.fingerprint,
            oos_dispersion_fingerprint(self.pipeline.dispersion),
        )
        self.assertEqual(
            self.pipeline.chains.fingerprint, oos_chain_fingerprint(self.pipeline.chains)
        )

    def test_readable_outputs(self) -> None:
        self.assertIn("OOS performance", self.pipeline.metrics.readable())
        self.assertIn("folds", self.pipeline.dispersion.readable())
        self.assertIn("chain", self.pipeline.chains.readable().lower())


class CrossMarketIntegrationTests(unittest.TestCase):
    """Cross-market end to end: consistency, reconciliation and single-market."""

    def setUp(self) -> None:
        data = _MockMarketData()
        self.hk = _run_pipeline(data, market=Market.HK, base_currency=Currency.HKD)
        self.us = _run_pipeline(data, market=Market.US, base_currency=Currency.HKD)

    def test_runs_share_one_context(self) -> None:
        self.assertEqual(self.hk.oos_run.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(self.us.oos_run.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(self.hk.oos_run.code_version, _CODE_VERSION)
        self.assertEqual(len(self.hk.oos_run), len(self.us.oos_run))

    def test_cross_market_paths_reconcile(self) -> None:
        report = _reconcile({Market.HK: self.hk, Market.US: self.us})
        self.assertTrue(report.reconciled)
        self.assertEqual(report.executed_fold_count, 8)
        by_market = {market_result.market: market_result for market_result in report.market_results}
        for market, pipeline in ((Market.HK, self.hk), (Market.US, self.us)):
            expected = sum(
                len(_trading_days_for(market, result.fold)) for result in pipeline.oos_run.results
            )
            self.assertEqual(by_market[market].oos_trading_days, expected)

    def test_single_market_reconcile(self) -> None:
        report = reconcile_cross_market_oos(
            {Market.HK: self.hk.oos_run},
            base_currency=Currency.HKD,
            calendar=_calendar(),
            fx_rate_for=_fx_rate_for,
            trading_days_for=_trading_days_for,
            cost_model_for=_cost_model_for,
            corporate_actions_for=_corporate_actions_for,
        )
        self.assertTrue(report.reconciled)
        self.assertEqual(report.markets, (Market.HK,))


if __name__ == "__main__":
    unittest.main()
