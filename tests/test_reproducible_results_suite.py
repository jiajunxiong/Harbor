"""Reproducible results test suite (MVP 3 / SP 3.80).

With a fixed data manifest (dataset fingerprint), config, code version and
seed executed twice, the trials (试验), OOS, stress (压力) and report artifacts
(报告产物) are completely identical (固定数据清单、配置、代码版本和种子，连续两次
得到完全相同的试验、OOS、压力和报告产物).

A single :func:`_run` executes the full stack — SP 3.33 rolling training
(trials), SP 3.35 OOS execution, the SP 3.51–3.57 stress families + SP 3.59
registry + SP 3.58 conclusion, and the SP 3.37/3.38 concatenated OOS metrics
report — over fixed inputs and captures every artifact plus its derived
fingerprint into a frozen :class:`ReplayResult`. Running it twice yields
byte-identical outputs (the acceptance); every derived fingerprint is
re-derivable, and negative controls (different seed / cost-stress level /
benchmark / dataset fingerprint) prove the determinism is anchored to the
inputs rather than vacuous. No database is required.
"""

import hashlib
import unittest
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType

from harbor.core.adjustments import ActionTerms
from harbor.core.backtest_config import BenchmarkKind
from harbor.core.backtest_domain import Currency, Fill, Market, NetValue, Order, OrderSide
from harbor.core.backtest_interfaces import DailyQuote, TradingCalendar
from harbor.core.benchmark import BenchmarkLevel, BenchmarkSeries
from harbor.core.calendar_stress import (
    CalendarStressScenarioResult,
    default_calendar_stresses,
    quantify_calendar_stress,
)
from harbor.core.corporate_action_stress import (
    CorporateActionStressInput,
    CorporateActionStressScenarioResult,
    default_corporate_action_stresses,
    quantify_corporate_action_stress,
)
from harbor.core.cost_hk import hk_order_cost
from harbor.core.cost_stress import (
    CostStressScenarioResult,
    default_cost_stresses,
    quantify_cost_stress,
)
from harbor.core.cost_us import us_order_cost
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.drawdown_events import DrawdownConfig, DrawdownSeries
from harbor.core.exposure import ExposurePoint, ExposureSeries
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.fx_stress import (
    FxStressScenarioResult,
    default_fx_stresses,
    quantify_fx_stress,
)
from harbor.core.holdout_registry import register_test_set
from harbor.core.liquidity_stress import (
    ExecutionDay,
    LiquidityStressScenarioResult,
    ValuationDay,
    default_liquidity_stresses,
    quantify_liquidity_stress,
)
from harbor.core.market_registry import CorporateActionType
from harbor.core.oos_concat import (
    OosEquityPath,
    concatenate_fold_oos,
    oos_concat_fingerprint,
)
from harbor.core.oos_performance import (
    OosPerformanceReport,
    compute_oos_metrics,
    oos_metrics_fingerprint,
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
from harbor.core.stability_rule import (
    StabilityConclusion,
    StabilitySignals,
    adjudicate_stability,
    default_stability_rule,
    stability_fingerprint,
)
from harbor.core.stock_pool import StockPoolMembership
from harbor.core.stock_pool_stress import (
    StockPoolStressInput,
    StockPoolStressScenarioResult,
    default_stock_pool_stresses,
    quantify_stock_pool_stress,
)
from harbor.core.stress_registry import (
    StressScenarioCategory,
    StressScenarioRegistry,
    build_scenario_registration,
    build_stress_registry,
    registry_fingerprint,
)
from harbor.core.test_access_guard import AccessGuard
from harbor.core.trade_metrics import TradeStats
from harbor.core.trading_calendar import MarketTradingCalendar
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
_FINGERPRINT2 = "e" * 64
_CODE_VERSION = "1.0.0"
_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ANCHORS = (date(2026, 1, 2), date(2026, 1, 3), date(2026, 4, 6), date(2026, 7, 1))


def _each_day(start: date, end: date) -> tuple[date, ...]:
    """Return every calendar day in the inclusive range."""
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return tuple(days)


# ---------------------------------------------------------------------------
# OOS bootstrap (the frozen data manifest; identical to the per-SP fixtures).
# ---------------------------------------------------------------------------


def _net_values_for(fold) -> tuple[NetValue, ...]:
    """Varying, ledger-consistent OOS net values (cash == securities)."""
    factor = 1.0 + 0.5 * fold.fold_index
    return tuple(
        NetValue(
            as_of_date=day,
            currency=Currency.HKD,
            cash=1_000_000.0 * 0.5 * (1.0 + 0.001 * factor * index),
            securities_value=1_000_000.0 * 0.5 * (1.0 + 0.001 * factor * index),
        )
        for index, day in enumerate(_each_day(fold.test_start, fold.test_end))
    )


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
    """Build the SP 3.31 fold sequence over the frozen manifest fingerprint."""
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


def _tuning(**overrides: object) -> TuningConfig:
    """Return the pre-registered tuning config (seed is part of trial identity)."""
    fields: dict[str, object] = {
        "primary_metric": "sharpe",
        "metric_direction": MetricDirection.HIGHER_BETTER,
        "max_trials": 3,
        "random_seed": 42,
        "min_validation_days": 63,
    }
    fields.update(overrides)
    return TuningConfig(**fields)  # type: ignore[arg-type]


def _candidates() -> list[dict[str, object]]:
    return [
        {"cash_weight": 0.05, "factor_weight": 0.95, "lookback": 252},
        {"cash_weight": 0.10, "factor_weight": 0.90, "lookback": 252},
        {"cash_weight": 0.05, "factor_weight": 0.95, "lookback": 324},
    ]


def _make_fit_factory(fingerprint: str):
    """A training fit carrying the frozen dataset fingerprint (SP 3.33)."""

    def fit_factory(train_start: date, train_end: date):
        return build_training_fit(
            fit_start=train_start,
            fit_end=train_end,
            dataset_fingerprint=fingerprint,
            code_version=_CODE_VERSION,
            fitted_state=(("lookback", 252.0),),
        )

    return fit_factory


def _evaluate_stub(fold, parameters: dict[str, object]) -> float:
    """Deterministic validation metric: larger lookback scores higher."""
    return int(parameters["lookback"]) / 1000.0


def _training_run(*, fingerprint: str = _FINGERPRINT, seed: int = 42) -> RollingTrainRun:
    """Run the SP 3.33 rolling training (trials) with fixed inputs."""
    return run_rolling_training(
        sequence=_sequence(dataset_fingerprint=fingerprint),
        space=_space(),
        budget=_budget(),
        market=Market.HK,
        dataset_fingerprint=fingerprint,
        code_version=_CODE_VERSION,
        tuning=_tuning(random_seed=seed),
        candidate_parameter_sets=_candidates(),
        fit_factory=_make_fit_factory(fingerprint),
        evaluate=_evaluate_stub,
        constraints=(),
        validation_samples=200,
    )


def _applied_standardization(decision_date: date) -> AppliedStandardization:
    return AppliedStandardization(
        decision_date=decision_date,
        scores=(("AAA", 0.5), ("BBB", -0.5)),
        method=StandardizationMethod.ZSCORE,
    )


def _application(fold_result, decision_date: date) -> ValidationApplication:
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
    fields: dict[str, object] = {
        "test_set_id": "holdout-1",
        "split": _split(),
        "config_hash": "cfg-hash",
        "created_at": _AT,
    }
    fields.update(overrides)
    return register_test_set(**fields)  # type: ignore[arg-type]


def _guard(**overrides: object) -> AccessGuard:
    fields: dict[str, object] = {"registration": _registration()}
    fields.update(overrides)
    return AccessGuard(**fields)  # type: ignore[arg-type]


def _manifest(fold, run_id: str) -> ReplayManifest:
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


def _run_engine(fold, selected) -> OosRunOutcome:
    run_id = f"oos-run-{fold.fold_index}"
    return OosRunOutcome(run_id=run_id, replay_manifest=_manifest(fold, run_id))


def _oos_run(training_run: RollingTrainRun, *, fingerprint: str) -> RollingOosRun:
    """Run SP 3.34 validation + SP 3.35 OOS execution with fixed inputs."""
    validation = run_rolling_validation(
        training_run,
        application_factory=_application,
        compute_validation=_compute_validation,
    )
    return run_rolling_oos(
        validation,
        guard=_guard(),
        current_stage=ValidationStatus.TEST_LOCKED,
        run_engine=_run_engine,
        requested_at=_AT,
    )


def _trade_stats(path: OosEquityPath) -> TradeStats:
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


# ---------------------------------------------------------------------------
# Per-family stress fixtures (SP 3.51–3.56), compact deterministic inputs.
# ---------------------------------------------------------------------------


def _hk_cost_fill(fold, *, side: OrderSide, quantity: float, price: float, day_offset: int) -> Fill:
    return Fill(
        order_ref=f"hk-{fold.fold_index}-{side.value}",
        symbol="0001.HK",
        market=Market.HK,
        side=side,
        quantity=quantity,
        price=price,
        currency=Currency.HKD,
        trade_date=fold.test_start + timedelta(days=day_offset),
        fee=hk_order_cost(symbol="0001.HK", side=side, quantity=quantity, price=price).total_fee,
    )


def _us_cost_fill(fold, *, side: OrderSide, quantity: float, price: float, day_offset: int) -> Fill:
    return Fill(
        order_ref=f"us-{fold.fold_index}-{side.value}",
        symbol="AAPL",
        market=Market.US,
        side=side,
        quantity=quantity,
        price=price,
        currency=Currency.USD,
        trade_date=fold.test_start + timedelta(days=day_offset),
        fee=us_order_cost(symbol="AAPL", side=side, quantity=quantity, price=price).total_cost,
    )


def _mixed_fills_for(fold) -> tuple[Fill, ...]:
    return (
        _hk_cost_fill(fold, side=OrderSide.BUY, quantity=1000.0, price=50.0, day_offset=10),
        _us_cost_fill(fold, side=OrderSide.SELL, quantity=10.0, price=100.0, day_offset=12),
    )


def _order(fold, market: Market, *, quantity: float, day_offset: int, ref: str = "o") -> Order:
    symbol = "0001.HK" if market is Market.HK else "AAPL"
    currency = Currency.HKD if market is Market.HK else Currency.USD
    return Order(
        symbol=symbol,
        market=market,
        side=OrderSide.BUY,
        quantity=quantity,
        currency=currency,
        trade_date=fold.test_start + timedelta(days=day_offset),
        ref=f"{ref}-{fold.fold_index}",
    )


def _quote(fold, market: Market, day_offset: int, *, volume: int = 100000, close: float = 50.0):
    day = fold.test_start + timedelta(days=day_offset)
    symbol = "0001.HK" if market is Market.HK else "AAPL"
    return DailyQuote(
        market=market,
        symbol=symbol,
        day=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        adjusted_close=close,
    )


def _execution_days(fold, market: Market) -> tuple[ExecutionDay, ...]:
    return (
        ExecutionDay(
            order=_order(fold, market, quantity=1000.0, day_offset=10, ref="big"),
            quote=_quote(fold, market, 10, volume=10000),
            volume=10000,
            reference_price=50.0,
        ),
        ExecutionDay(
            order=_order(fold, market, quantity=100.0, day_offset=12, ref="full"),
            quote=_quote(fold, market, 12, volume=100000),
            volume=100000,
            reference_price=50.0,
        ),
        ExecutionDay(
            order=_order(fold, market, quantity=200.0, day_offset=14, ref="susp"),
            quote=None,
            volume=0,
            reference_price=50.0,
        ),
    )


def _valuation_days(fold, market: Market) -> tuple[ValuationDay, ...]:
    symbol = "0001.HK" if market is Market.HK else "AAPL"
    return (
        ValuationDay(
            market=market,
            symbol=symbol,
            day=fold.test_start + timedelta(days=16),
            quote=None,
            last_quote=_quote(fold, market, 15, volume=5000, close=49.5),
        ),
        ValuationDay(
            market=market,
            symbol=symbol,
            day=fold.test_start + timedelta(days=17),
            quote=_quote(fold, market, 17, volume=5000),
            last_quote=None,
        ),
    )


def _us_fill_fx(fold) -> Fill:
    return Fill(
        order_ref=f"fx-us-{fold.fold_index}",
        symbol="AAPL",
        market=Market.US,
        side=OrderSide.BUY,
        quantity=10.0,
        price=100.0,
        currency=Currency.USD,
        trade_date=fold.test_start + timedelta(days=10),
        fee=1.0,
    )


def _fx_usd_hkd(from_currency, to_currency, as_of):
    if from_currency is Currency.USD and to_currency is Currency.HKD:
        return 7.8
    return None


def _calendar_factory(market: Market):
    def factory(holidays: frozenset[date]) -> TradingCalendar:
        return MarketTradingCalendar(holidays={market: holidays})

    return factory


def _ca_event(market: Market, **overrides: object) -> CorporateActionStressInput:
    fields: dict[str, object] = {
        "symbol": "0001.HK" if market is Market.HK else "AAPL",
        "action_id": f"ca-{market.value}-1",
        "action_type": CorporateActionType.RIGHTS_ISSUE,
        "snapshot_date": date(2026, 1, 10),
        "terms": ActionTerms(),
        "record_date": None,
        "ex_date": date(2026, 1, 15),
        "registered_at": None,
        "pending_review": False,
        "adjustment_factor": None,
        "expected_adjustment": None,
    }
    fields.update(overrides)
    return CorporateActionStressInput(**fields)  # type: ignore[arg-type]


def _membership(market: Market, symbol: str, effective: date) -> StockPoolMembership:
    return StockPoolMembership(
        market=market,
        symbol=symbol,
        effective_date=effective,
        expiry_date=None,
        source="pool",
    )


def _pool(market: Market, **overrides: object) -> StockPoolStressInput:
    active = ("0001.HK", "0005.HK") if market is Market.HK else ("AAPL", "MSFT")
    fields: dict[str, object] = {
        "market": market,
        "memberships": (
            _membership(market, active[0], date(2019, 1, 1)),
            _membership(market, active[1], date(2019, 1, 1)),
            _membership(market, "GONE", date(2019, 1, 1)),
        ),
        "expected_universe": (active[0], active[1], "GONE", "MISSING"),
        "as_of": date(2026, 1, 10),
        "historical_known": True,
    }
    fields.update(overrides)
    return StockPoolStressInput(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The full replayable stress stack (SP 3.51–3.59).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StressResult:
    """Every stress artifact of one run (six families + registry + conclusion)."""

    cost: CostStressScenarioResult
    liquidity: LiquidityStressScenarioResult
    fx: FxStressScenarioResult
    calendar: CalendarStressScenarioResult
    corporate_action: CorporateActionStressScenarioResult
    stock_pool: StockPoolStressScenarioResult
    registry: StressScenarioRegistry
    conclusion: StabilityConclusion
    fingerprint: str


def _run_stress(oos: RollingOosRun, *, cost_index: int = 0) -> _StressResult:
    """Quantify the six stress families, register them and adjudicate (SP 3.58)."""
    cost = quantify_cost_stress(
        oos,
        stress_config=default_cost_stresses()[cost_index],
        fills_for=_mixed_fills_for,
        net_values_for=_net_values_for,
        base_currency=Currency.HKD,
        fx_rate_for=None,
    )
    liquidity = quantify_liquidity_stress(
        oos,
        stress_config=default_liquidity_stresses()[0],
        orders_for=lambda fold: _execution_days(fold, Market.HK) + _execution_days(fold, Market.US),
        valuations_for=lambda fold: (
            _valuation_days(fold, Market.HK) + _valuation_days(fold, Market.US)
        ),
    )
    fx = quantify_fx_stress(
        oos,
        stress_config=default_fx_stresses()[0],
        base_currency=Currency.HKD,
        fills_for=lambda fold: (_us_fill_fx(fold),),
        net_values_for=_net_values_for,
        fx_rate_for=_fx_usd_hkd,
    )
    calendar = quantify_calendar_stress(
        oos,
        stress_config=default_calendar_stresses()[0],
        market=Market.HK,
        anchors=_ANCHORS,
        calendar_factory=_calendar_factory(Market.HK),
    )
    corporate_action = quantify_corporate_action_stress(
        oos,
        stress_config=default_corporate_action_stresses()[0],
        market=Market.HK,
        events=(_ca_event(Market.HK),),
    )
    stock_pool = quantify_stock_pool_stress(
        oos,
        stress_config=default_stock_pool_stresses()[0],
        pool=_pool(Market.US),
    )
    registrations = (
        build_scenario_registration(
            category=StressScenarioCategory.COST,
            scenario_id=cost.stress.version,
            market=Market.HK,
            assumptions=("rates scaled by the pre-registered multiplier",),
            parameters={"multiplier": 2.0},
            dataset_fingerprint=oos.dataset_fingerprint,
            code_version=oos.code_version,
            baseline_difference=cost.net_value_impact_pct,
            difference_summary=None,
        ),
        build_scenario_registration(
            category=StressScenarioCategory.LIQUIDITY,
            scenario_id=liquidity.stress.version,
            market=Market.HK,
            assumptions=("participation rate tightened",),
            parameters={"participation_rate": liquidity.stress.participation_rate},
            dataset_fingerprint=oos.dataset_fingerprint,
            code_version=oos.code_version,
            baseline_difference=None,
            difference_summary=(
                f"unfilled {liquidity.unfilled_quantity:.2f}; "
                f"refused {liquidity.refused_quantity:.2f}"
            ),
        ),
        build_scenario_registration(
            category=StressScenarioCategory.FX,
            scenario_id=fx.stress.version,
            market=Market.HK,
            assumptions=("FX shock applied to the foreign leg",),
            parameters={"shock_bps": fx.stress.shock_bps},
            dataset_fingerprint=oos.dataset_fingerprint,
            code_version=oos.code_version,
            baseline_difference=fx.net_value_impact_pct,
            difference_summary=None,
        ),
        build_scenario_registration(
            category=StressScenarioCategory.CALENDAR,
            scenario_id=calendar.stress.version,
            market=Market.HK,
            assumptions=("closure holiday added to the market calendar",),
            parameters={"holidays": "2026-01-02"},
            dataset_fingerprint=oos.dataset_fingerprint,
            code_version=oos.code_version,
            baseline_difference=None,
            difference_summary=(
                f"deferred {calendar.deferred_count}; stress-closed {calendar.stress_closed_count}"
            ),
        ),
        build_scenario_registration(
            category=StressScenarioCategory.CORPORATE_ACTION,
            scenario_id=corporate_action.stress.version,
            market=Market.HK,
            assumptions=("missing terms are a key unknown",),
            parameters={"kind": corporate_action.stress.kind.value},
            dataset_fingerprint=oos.dataset_fingerprint,
            code_version=oos.code_version,
            baseline_difference=None,
            difference_summary=f"{corporate_action.finding_count} finding(s)",
        ),
        build_scenario_registration(
            category=StressScenarioCategory.STOCK_POOL,
            scenario_id=stock_pool.stress.version,
            market=Market.US,
            assumptions=("the tradeable universe shrinks",),
            parameters={"kind": stock_pool.stress.kind.value},
            dataset_fingerprint=oos.dataset_fingerprint,
            code_version=oos.code_version,
            baseline_difference=stock_pool.impact_pct,
            difference_summary=None,
        ),
    )
    registry = build_stress_registry(version="reg-stress", registrations=registrations)
    signals = StabilitySignals(
        market=Market.HK,
        dataset_fingerprint=oos.dataset_fingerprint,
        code_version=oos.code_version,
        fold_spread=0.10,
        fold_count=len(oos.results),
        fold_failure_count=0,
        neighborhood_cliff_ratio=0.1,
        neighborhood_infeasible_ratio=0.0,
        environment_insufficient_ratio=0.0,
        max_stress_loss_pct=5.0,
        stress_unquantifiable=False,
        coverage_blocked=False,
    )
    conclusion = adjudicate_stability(signals, config=default_stability_rule())
    payload = "|".join(
        (
            cost.fingerprint,
            liquidity.fingerprint,
            fx.fingerprint,
            calendar.fingerprint,
            corporate_action.fingerprint,
            stock_pool.fingerprint,
            registry.fingerprint,
            conclusion.fingerprint,
        )
    )
    return _StressResult(
        cost=cost,
        liquidity=liquidity,
        fx=fx,
        calendar=calendar,
        corporate_action=corporate_action,
        stock_pool=stock_pool,
        registry=registry,
        conclusion=conclusion,
        fingerprint=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    )


# ---------------------------------------------------------------------------
# The full replayable run capturing trials + OOS + stress + report.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayResult:
    """Every artifact of one full run (trials / OOS / stress / report)."""

    trials: tuple[object, ...]
    trial_fingerprints: tuple[str, ...]
    oos_run: RollingOosRun
    oos_fingerprint: str
    path: OosEquityPath
    path_fingerprint: str
    report: OosPerformanceReport
    report_fingerprint: str
    stress: _StressResult
    fingerprint: str


def _run(
    *,
    fingerprint: str = _FINGERPRINT,
    seed: int = 42,
    cost_index: int = 0,
    benchmark_return: float = 0.02,
) -> ReplayResult:
    """Execute the full stack once over the frozen manifest, config and seed."""
    training = _training_run(fingerprint=fingerprint, seed=seed)
    trials = tuple(trial for result in training.results for trial in result.trials)
    trial_fingerprints = tuple(trial_fingerprint(trial) for trial in trials)
    oos = _oos_run(training, fingerprint=fingerprint)
    oos_fingerprint = rolling_oos_fingerprint(oos)
    path = concatenate_fold_oos(oos, net_values_for=_net_values_for)
    path_fingerprint = oos_concat_fingerprint(path)
    report = compute_oos_metrics(
        path,
        trade_stats_for=_trade_stats,
        exposure_for=_exposure,
        benchmark_return_for=lambda _path: benchmark_return,
    )
    report_fingerprint = oos_metrics_fingerprint(report)
    stress = _run_stress(oos, cost_index=cost_index)
    fingerprint_ = _result_fingerprint(
        trial_fingerprints,
        oos_fingerprint,
        path_fingerprint,
        report_fingerprint,
        stress.fingerprint,
    )
    return ReplayResult(
        trials=trials,
        trial_fingerprints=trial_fingerprints,
        oos_run=oos,
        oos_fingerprint=oos_fingerprint,
        path=path,
        path_fingerprint=path_fingerprint,
        report=report,
        report_fingerprint=report_fingerprint,
        stress=stress,
        fingerprint=fingerprint_,
    )


def _result_fingerprint(
    trial_fingerprints: tuple[str, ...],
    oos_fingerprint: str,
    path_fingerprint: str,
    report_fingerprint: str,
    stress_fingerprint: str,
) -> str:
    payload = "|".join(
        (
            ",".join(trial_fingerprints),
            oos_fingerprint,
            path_fingerprint,
            report_fingerprint,
            stress_fingerprint,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class FullReplayTests(unittest.TestCase):
    """THE acceptance: with fixed inputs, two runs are completely identical."""

    def test_two_runs_are_identical(self) -> None:
        self.assertEqual(_run(), _run())

    def test_trials_replay_identical(self) -> None:
        first, second = _run(), _run()
        self.assertEqual(first.trials, second.trials)
        self.assertEqual(first.trial_fingerprints, second.trial_fingerprints)
        self.assertEqual(len(first.trials), 3 * 4)  # 3 candidates × 4 folds

    def test_oos_replay_identical(self) -> None:
        first, second = _run(), _run()
        self.assertEqual(first.oos_run, second.oos_run)
        self.assertEqual(first.oos_fingerprint, second.oos_fingerprint)
        self.assertEqual(first.path, second.path)
        self.assertEqual(first.path_fingerprint, second.path_fingerprint)

    def test_stress_replay_identical(self) -> None:
        first, second = _run(), _run()
        self.assertEqual(first.stress, second.stress)
        self.assertEqual(first.stress.registry, second.stress.registry)
        self.assertEqual(first.stress.conclusion, second.stress.conclusion)

    def test_report_replay_identical(self) -> None:
        first, second = _run(), _run()
        self.assertEqual(first.report, second.report)
        self.assertEqual(first.report_fingerprint, second.report_fingerprint)

    def test_fingerprints_are_rederivable(self) -> None:
        result = _run()
        self.assertEqual(result.oos_fingerprint, rolling_oos_fingerprint(result.oos_run))
        self.assertEqual(result.path_fingerprint, oos_concat_fingerprint(result.path))
        self.assertEqual(result.report_fingerprint, oos_metrics_fingerprint(result.report))
        self.assertEqual(
            result.stress.fingerprint,
            hashlib.sha256(
                "|".join(
                    (
                        result.stress.cost.fingerprint,
                        result.stress.liquidity.fingerprint,
                        result.stress.fx.fingerprint,
                        result.stress.calendar.fingerprint,
                        result.stress.corporate_action.fingerprint,
                        result.stress.stock_pool.fingerprint,
                        result.stress.registry.fingerprint,
                        result.stress.conclusion.fingerprint,
                    )
                ).encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(
            result.stress.conclusion.fingerprint,
            stability_fingerprint(result.stress.conclusion),
        )
        self.assertEqual(
            result.stress.registry.fingerprint,
            registry_fingerprint(result.stress.registry),
        )
        self.assertEqual(
            result.fingerprint,
            _result_fingerprint(
                result.trial_fingerprints,
                result.oos_fingerprint,
                result.path_fingerprint,
                result.report_fingerprint,
                result.stress.fingerprint,
            ),
        )


class DeterminismControlTests(unittest.TestCase):
    """The equality is meaningful — a changed input changes the output."""

    def test_seed_change_alters_trials(self) -> None:
        first, second = _run(seed=42), _run(seed=7)
        self.assertNotEqual(first.trial_fingerprints, second.trial_fingerprints)
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_cost_stress_level_alters_stress(self) -> None:
        first, second = _run(cost_index=0), _run(cost_index=2)
        self.assertNotEqual(first.stress.fingerprint, second.stress.fingerprint)
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_benchmark_change_alters_report(self) -> None:
        first, second = _run(benchmark_return=0.02), _run(benchmark_return=0.05)
        self.assertNotEqual(first.report_fingerprint, second.report_fingerprint)
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_dataset_fingerprint_change_alters_oos(self) -> None:
        first, second = _run(fingerprint=_FINGERPRINT), _run(fingerprint=_FINGERPRINT2)
        self.assertNotEqual(first.oos_fingerprint, second.oos_fingerprint)
        self.assertNotEqual(first.fingerprint, second.fingerprint)


if __name__ == "__main__":
    unittest.main()
