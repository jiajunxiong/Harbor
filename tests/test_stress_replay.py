"""Stress-test replayability tests (MVP 3 / SP 3.63, TEST-ONLY).

Replays the full stress stack — the six stress scenario families (SP 3.51–3.56)
over the same frozen data manifest and baseline, the SP 3.59 scenario registry
(difference table / 与基线的差异) and the SP 3.58 stability conclusion — and
confirms that executing the same data manifest, baseline and scenario configs
repeatedly yields completely identical difference tables and conclusions
(相同数据清单、基线和情景配置重复执行，差异表和结论完全一致).

Every run executes the SP 3.35 OOS bootstrap exactly once, quantifies the six
stress families, registers their baseline differences in an SP 3.59 ledger and
adjudicates the SP 3.58 conclusion from the derived signals. A frozen
:class:`StressReplayResult` captures every artifact plus a top-level replay
fingerprint derived from the per-artifact fingerprints, so two full replays are
value-equal if and only if every scenario, the difference table and the
conclusion are identical.
"""

import hashlib
import unittest
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone

from harbor.core.adjustments import ActionTerms
from harbor.core.backtest_config import BenchmarkKind
from harbor.core.backtest_domain import (
    Currency,
    Fill,
    Market,
    NetValue,
    Order,
    OrderSide,
)
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
    cost_stress_fingerprint,
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
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.fx_stress import (
    FxStressScenarioResult,
    default_fx_stresses,
    fx_stress_fingerprint,
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
    stock_pool_stress_fingerprint,
)
from harbor.core.stress_registry import (
    StressScenarioCategory,
    StressScenarioRegistry,
    build_scenario_registration,
    build_stress_registry,
    registry_fingerprint,
)
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
    OOSConclusion,
    ValidationStatus,
)

_FINGERPRINT = "f" * 64
_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_OOS_START = date(2023, 1, 1)
_OOS_END = date(2026, 12, 30)
_ANCHORS = (date(2026, 1, 2), date(2026, 1, 3), date(2026, 4, 6), date(2026, 7, 1))


# ---------------------------------------------------------------------------
# Shared OOS bootstrap (the frozen data manifest; identical to the per-SP
# fixtures so the replay is deterministic).
# ---------------------------------------------------------------------------


def _each_day(start: date, end: date) -> tuple[date, ...]:
    """Return every calendar day in the inclusive range."""
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return tuple(days)


def _net_values_for(fold) -> tuple[NetValue, ...]:
    """Constant OOS net values (a flat 1,000,000 path)."""
    return tuple(
        NetValue(
            as_of_date=day,
            currency=Currency.HKD,
            cash=1_000_000.0,
            securities_value=0.0,
        )
        for day in _each_day(fold.test_start, fold.test_end)
    )


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


def _evaluate_stub(fold, parameters: dict[str, object]) -> float:
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
        "evaluate": _evaluate_stub,
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


# ---------------------------------------------------------------------------
# Per-family stress fixtures (SP 3.51–3.56).
# ---------------------------------------------------------------------------


def _hk_cost_fill(fold, *, side: OrderSide, quantity: float, price: float, day_offset: int) -> Fill:
    """A deterministic HK fill with the default-cost baseline fee."""
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
    """A deterministic US fill with the default-cost baseline fee."""
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
    """An HK buy and a US sell in the same fold."""
    return (
        _hk_cost_fill(fold, side=OrderSide.BUY, quantity=1000.0, price=50.0, day_offset=10),
        _us_cost_fill(fold, side=OrderSide.SELL, quantity=10.0, price=100.0, day_offset=12),
    )


def _order(
    fold,
    market: Market,
    *,
    quantity: float,
    day_offset: int,
    ref: str = "o",
) -> Order:
    """A deterministic buy order for one market on a day inside the fold."""
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


def _quote(
    fold,
    market: Market,
    day_offset: int,
    *,
    volume: int = 100000,
    close: float = 50.0,
) -> DailyQuote:
    """A deterministic quote for one market on a day inside the fold."""
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
    """Three orders per fold: a participation-capped buy, a full buy, a refusal."""
    big = ExecutionDay(
        order=_order(fold, market, quantity=1000.0, day_offset=10, ref="big"),
        quote=_quote(fold, market, 10, volume=10000),
        volume=10000,
        reference_price=50.0,
    )
    full = ExecutionDay(
        order=_order(fold, market, quantity=100.0, day_offset=12, ref="full"),
        quote=_quote(fold, market, 12, volume=100000),
        volume=100000,
        reference_price=50.0,
    )
    suspended = ExecutionDay(
        order=_order(fold, market, quantity=200.0, day_offset=14, ref="susp"),
        quote=None,
        volume=0,
        reference_price=50.0,
    )
    return (big, full, suspended)


def _valuation_days(fold, market: Market) -> tuple[ValuationDay, ...]:
    """Two valuations: one carried-forward (warning), one quoted (no warning)."""
    symbol = "0001.HK" if market is Market.HK else "AAPL"
    missing_day = fold.test_start + timedelta(days=16)
    return (
        ValuationDay(
            market=market,
            symbol=symbol,
            day=missing_day,
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


def _us_fill_fx(fold, *, quantity: float = 10.0, price: float = 100.0) -> Fill:
    """A deterministic US (foreign) fill inside the fold."""
    return Fill(
        order_ref=f"fx-us-{fold.fold_index}",
        symbol="AAPL",
        market=Market.US,
        side=OrderSide.BUY,
        quantity=quantity,
        price=price,
        currency=Currency.USD,
        trade_date=fold.test_start + timedelta(days=10),
        fee=1.0,
    )


def _fx_usd_hkd(from_currency, to_currency, as_of):
    """A constant USD->HKD rate of 7.8 (missing for other pairs)."""
    if from_currency is Currency.USD and to_currency is Currency.HKD:
        return 7.8
    return None


def _calendar_factory(market: Market):
    """A factory building a weekday calendar with the given market holidays."""

    def factory(holidays: frozenset[date]) -> TradingCalendar:
        return MarketTradingCalendar(holidays={market: holidays})

    return factory


def _ca_event(
    market: Market,
    *,
    action_type: CorporateActionType,
    **overrides: object,
) -> CorporateActionStressInput:
    """A minimal corporate-action event with missing terms (SP 3.55)."""
    symbol = "0001.HK" if market is Market.HK else "AAPL"
    fields: dict[str, object] = {
        "symbol": symbol,
        "action_id": f"ca-{market.value}-1",
        "action_type": action_type,
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


def _membership(
    market: Market,
    symbol: str,
    effective: date,
    expiry: date | None = None,
) -> StockPoolMembership:
    """A stock-pool membership window for one market."""
    return StockPoolMembership(
        market=market,
        symbol=symbol,
        effective_date=effective,
        expiry_date=expiry,
        source="pool",
    )


def _pool(market: Market, **overrides: object) -> StockPoolStressInput:
    """A pool with two active, two delisted-covered and one missing name."""
    active = ("0001.HK", "0005.HK") if market is Market.HK else ("AAPL", "MSFT")
    fields: dict[str, object] = {
        "market": market,
        "memberships": (
            _membership(market, active[0], date(2019, 1, 1)),
            _membership(market, active[1], date(2019, 1, 1)),
            _membership(market, "GONE", date(2019, 1, 1), date(2025, 12, 31)),
            _membership(market, "LOST", date(2019, 1, 1), date(2024, 6, 30)),
        ),
        "expected_universe": (active[0], active[1], "GONE", "LOST", "MISSING"),
        "as_of": date(2026, 1, 10),
        "historical_known": True,
    }
    fields.update(overrides)
    return StockPoolStressInput(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The full replayable stress run.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StressReplayResult:
    """Every artifact of one full stress run (SP 3.63)."""

    cost: CostStressScenarioResult
    liquidity: LiquidityStressScenarioResult
    fx: FxStressScenarioResult
    calendar: CalendarStressScenarioResult
    corporate_action: CorporateActionStressScenarioResult
    stock_pool: StockPoolStressScenarioResult
    registry: StressScenarioRegistry
    conclusion: StabilityConclusion
    fingerprint: str


def _result_fingerprint(result: StressReplayResult) -> str:
    """A replay fingerprint over every artifact fingerprint."""
    payload = "|".join(
        (
            result.cost.fingerprint,
            result.liquidity.fingerprint,
            result.fx.fingerprint,
            result.calendar.fingerprint,
            result.corporate_action.fingerprint,
            result.stock_pool.fingerprint,
            result.registry.fingerprint,
            result.conclusion.fingerprint,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run() -> StressReplayResult:
    """Execute the full stress stack once over the frozen manifest."""
    oos = _oos_run()
    cost = quantify_cost_stress(
        oos,
        stress_config=default_cost_stresses()[0],
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
        stress_config=default_fx_stresses()[2],
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
        events=(_ca_event(Market.HK, action_type=CorporateActionType.RIGHTS_ISSUE),),
    )
    stock_pool = quantify_stock_pool_stress(
        oos,
        stress_config=default_stock_pool_stresses()[2],
        pool=_pool(Market.US),
    )
    # The SP 3.59 difference table (与基线的差异) for every scenario.
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
    # The SP 3.58 conclusion from the derived stress signals.
    signals = StabilitySignals(
        market=Market.HK,
        dataset_fingerprint=oos.dataset_fingerprint,
        code_version=oos.code_version,
        fold_spread=0.10,
        fold_count=4,
        fold_failure_count=0,
        neighborhood_cliff_ratio=0.10,
        neighborhood_infeasible_ratio=0.10,
        environment_insufficient_ratio=0.10,
        max_stress_loss_pct=max(cost.net_value_impact_pct, fx.net_value_impact_pct),
        stress_unquantifiable=False,
        coverage_blocked=False,
    )
    conclusion = adjudicate_stability(signals, config=default_stability_rule())
    result = StressReplayResult(
        cost=cost,
        liquidity=liquidity,
        fx=fx,
        calendar=calendar,
        corporate_action=corporate_action,
        stock_pool=stock_pool,
        registry=registry,
        conclusion=conclusion,
        fingerprint="unfingerprinted",
    )
    return replace(result, fingerprint=_result_fingerprint(result))


class StressReplayTests(unittest.TestCase):
    """Replaying the same inputs yields identical artifacts (SP 3.63)."""

    def test_full_replay_is_identical(self) -> None:
        self.assertEqual(_run(), _run())

    def test_replay_fingerprint_stable(self) -> None:
        self.assertEqual(_run().fingerprint, _run().fingerprint)
        self.assertEqual(len(_run().fingerprint), 64)

    def test_each_scenario_identical_across_replay(self) -> None:
        first = _run()
        second = _run()
        self.assertEqual(first.cost, second.cost)
        self.assertEqual(first.liquidity, second.liquidity)
        self.assertEqual(first.fx, second.fx)
        self.assertEqual(first.calendar, second.calendar)
        self.assertEqual(first.corporate_action, second.corporate_action)
        self.assertEqual(first.stock_pool, second.stock_pool)

    def test_difference_table_identical_across_replay(self) -> None:
        first = _run()
        second = _run()
        # The SP 3.59 registry is the difference table (与基线的差异).
        self.assertEqual(first.registry, second.registry)
        self.assertEqual(first.registry.registrations, second.registry.registrations)
        self.assertEqual(first.registry.fingerprint, second.registry.fingerprint)
        # Every scenario records either a numeric difference or a summary.
        for registration in first.registry:
            self.assertTrue(
                registration.baseline_difference is not None or registration.difference_summary
            )

    def test_conclusion_identical_across_replay(self) -> None:
        first = _run()
        second = _run()
        self.assertEqual(first.conclusion, second.conclusion)
        self.assertEqual(first.conclusion.fingerprint, second.conclusion.fingerprint)

    def test_conclusion_qualified_on_stable_stress(self) -> None:
        self.assertEqual(_run().conclusion.conclusion, OOSConclusion.QUALIFIED)

    def test_artifact_fingerprints_rederivable(self) -> None:
        result = _run()
        self.assertEqual(result.cost.fingerprint, cost_stress_fingerprint(result.cost))
        self.assertEqual(result.fx.fingerprint, fx_stress_fingerprint(result.fx))
        self.assertEqual(
            result.stock_pool.fingerprint,
            stock_pool_stress_fingerprint(result.stock_pool),
        )
        self.assertEqual(result.registry.fingerprint, registry_fingerprint(result.registry))
        self.assertEqual(result.conclusion.fingerprint, stability_fingerprint(result.conclusion))

    def test_registry_embeds_six_scenario_differences(self) -> None:
        registry = _run().registry
        self.assertEqual(registry.count, 6)
        self.assertEqual(len(registry.for_category(StressScenarioCategory.COST)), 1)
        self.assertEqual(len(registry.for_category(StressScenarioCategory.FX)), 1)
        self.assertEqual(len(registry.for_category(StressScenarioCategory.STOCK_POOL)), 1)


if __name__ == "__main__":
    unittest.main()
