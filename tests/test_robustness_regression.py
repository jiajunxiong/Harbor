"""Robustness regression tests (MVP 3 / SP 3.60, TEST-ONLY).

Covers the six stress scenario families — cost (成本), liquidity (流动性), FX,
calendar (日历), corporate actions (企业行动) and stock pool (股票池) — and
confirms the HK and US rules are never collapsed into one simplified rule
(确认港美规则不会被统一简化). The shared OOS bootstrap mirrors the per-SP
fixtures (SP 3.51–3.56); each family is exercised against both markets and the
HK/US distinctness mechanism is asserted:

- cost: the stressed config carries SEPARATE HK and US ``CostConfig``s (HK board
  lot 100 vs US fractional lot 1); a mixed HK+US fill set re-prices every fill
  with its own market's cost function, so ``cost_increase`` is exactly the sum
  of the independent per-market increases.
- liquidity: the same tightened participation applies to BOTH markets' orders
  and neither market is dropped — the mixed-market aggregates equal the sum of
  the HK-only and US-only runs.
- fx: with HKD base the US fills are the foreign leg and with USD base the HK
  fills are; missing FX always refuses rather than assuming 1:1 in both
  directions.
- calendar: the stress holidays are injected per-market — a closure that closes
  HK leaves US open when only HK's calendar carries it, proving the calendars
  are not unified.
- corporate action: HK allows RIGHTS_ISSUE but not SPLIT and vice versa, so a
  MISSING_TERMS finding is produced only when the action type is allowed in
  that market.
- stock pool: both markets' pools are stressed and the result records the
  pool's market; an unknown-history pool blocks both markets identically.
"""

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from harbor.core.action_mapping import allowed_action_types
from harbor.core.adjustments import ActionTerms
from harbor.core.backtest_config import BenchmarkKind, CostConfig
from harbor.core.backtest_domain import (
    Currency,
    Fill,
    Market,
    NetValue,
    Order,
    OrderSide,
    to_market_target,
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
    compute_cost_stress_report,
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
from harbor.core.stock_pool import StockPoolMembership
from harbor.core.stock_pool_stress import (
    StockPoolStressInput,
    StockPoolStressScenarioResult,
    default_stock_pool_stresses,
    quantify_stock_pool_stress,
)
from harbor.core.stress_registry import (
    StressScenarioCategory,
    build_scenario_registration,
    build_stress_registry,
    require_scenarios_registered,
    scenario_refs_from_reports,
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
    CoverageSeverity,
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
_ANCHORS = (date(2026, 1, 2), date(2026, 1, 3), date(2026, 4, 6), date(2026, 7, 1))


# ---------------------------------------------------------------------------
# Shared OOS bootstrap (identical to the SP 3.51–3.56 per-SP fixtures).
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


def _symbol_for(market: Market) -> str:
    """A deterministic symbol for the market."""
    return "0700.HK" if market is Market.HK else "AAPL"


# ---------------------------------------------------------------------------
# Cost stress fixtures (SP 3.51).
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


def _hk_fills_for(fold) -> tuple[Fill, ...]:
    """One HK buy per fold."""
    return (_hk_cost_fill(fold, side=OrderSide.BUY, quantity=1000.0, price=50.0, day_offset=10),)


def _us_fills_for(fold) -> tuple[Fill, ...]:
    """One US sell per fold."""
    return (_us_cost_fill(fold, side=OrderSide.SELL, quantity=10.0, price=100.0, day_offset=12),)


def _mixed_fills_for(fold) -> tuple[Fill, ...]:
    """An HK buy and a US sell in the same fold."""
    return _hk_fills_for(fold) + _us_fills_for(fold)


def _cost_scenario(*, fills_for) -> CostStressScenarioResult:
    """Quantify the default 2x cost stress over the given fills."""
    return quantify_cost_stress(
        _oos_run(),
        stress_config=default_cost_stresses()[0],
        fills_for=fills_for,
        net_values_for=_net_values_for,
        base_currency=Currency.HKD,
        fx_rate_for=None,
    )


# ---------------------------------------------------------------------------
# Liquidity stress fixtures (SP 3.52).
# ---------------------------------------------------------------------------


def _order(
    fold,
    market: Market,
    *,
    quantity: float,
    day_offset: int,
    ref: str = "o",
) -> Order:
    """A deterministic buy order for one market on a day inside the fold."""
    symbol = _symbol_for(market)
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
    return DailyQuote(
        market=market,
        symbol=_symbol_for(market),
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
    missing_day = fold.test_start + timedelta(days=16)
    return (
        ValuationDay(
            market=market,
            symbol=_symbol_for(market),
            day=missing_day,
            quote=None,
            last_quote=_quote(fold, market, 15, volume=5000, close=49.5),
        ),
        ValuationDay(
            market=market,
            symbol=_symbol_for(market),
            day=fold.test_start + timedelta(days=17),
            quote=_quote(fold, market, 17, volume=5000),
            last_quote=None,
        ),
    )


def _liquidity_scenario(*, orders_for, valuations_for) -> LiquidityStressScenarioResult:
    """Quantify the default tight liquidity stress over the given inputs."""
    return quantify_liquidity_stress(
        _oos_run(),
        stress_config=default_liquidity_stresses()[0],
        orders_for=orders_for,
        valuations_for=valuations_for,
    )


# ---------------------------------------------------------------------------
# FX stress fixtures (SP 3.53).
# ---------------------------------------------------------------------------


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


def _hk_fill_fx(fold, *, quantity: float = 1000.0, price: float = 50.0) -> Fill:
    """A deterministic HK (foreign under a USD base) fill inside the fold."""
    return Fill(
        order_ref=f"fx-hk-{fold.fold_index}",
        symbol="0001.HK",
        market=Market.HK,
        side=OrderSide.BUY,
        quantity=quantity,
        price=price,
        currency=Currency.HKD,
        trade_date=fold.test_start + timedelta(days=10),
        fee=1.0,
    )


def _fx_usd_hkd(from_currency, to_currency, as_of):
    """A constant USD->HKD rate of 7.8 (missing for other pairs)."""
    if from_currency is Currency.USD and to_currency is Currency.HKD:
        return 7.8
    return None


def _fx_hkd_usd(from_currency, to_currency, as_of):
    """A constant HKD->USD rate of 1/7.8 (missing for other pairs)."""
    if from_currency is Currency.HKD and to_currency is Currency.USD:
        return 1.0 / 7.8
    return None


def _fx_none(from_currency, to_currency, as_of):
    """A feed with no FX rate at all."""
    return None


def _fx_scenario(*, base_currency, fills_for, fx_rate_for) -> FxStressScenarioResult:
    """Quantify the default +5% FX shock stress over the given fills."""
    return quantify_fx_stress(
        _oos_run(),
        stress_config=default_fx_stresses()[2],
        base_currency=base_currency,
        fills_for=fills_for,
        net_values_for=_net_values_for,
        fx_rate_for=fx_rate_for,
    )


# ---------------------------------------------------------------------------
# Calendar stress fixtures (SP 3.54).
# ---------------------------------------------------------------------------


def _calendar_factory(market: Market):
    """A factory building a weekday calendar with the given market holidays."""

    def factory(holidays: frozenset[date]) -> TradingCalendar:
        return MarketTradingCalendar(holidays={market: holidays})

    return factory


def _calendar_scenario(*, market, calendar_factory) -> CalendarStressScenarioResult:
    """Quantify the default closure stress for one market's calendar."""
    return quantify_calendar_stress(
        _oos_run(),
        stress_config=default_calendar_stresses()[0],
        market=market,
        anchors=_ANCHORS,
        calendar_factory=calendar_factory,
    )


# ---------------------------------------------------------------------------
# Corporate-action stress fixtures (SP 3.55).
# ---------------------------------------------------------------------------


def _ca_event(
    market: Market,
    *,
    action_type: CorporateActionType,
    **overrides: object,
) -> CorporateActionStressInput:
    """A minimal corporate-action event with missing terms (SP 3.55)."""
    fields: dict[str, object] = {
        "symbol": _symbol_for(market),
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


def _ca_scenario(*, market, events) -> CorporateActionStressScenarioResult:
    """Quantify the default missing-terms scenario over the given events."""
    return quantify_corporate_action_stress(
        _oos_run(),
        stress_config=default_corporate_action_stresses()[0],
        market=market,
        events=events,
    )


# ---------------------------------------------------------------------------
# Stock-pool stress fixtures (SP 3.56).
# ---------------------------------------------------------------------------


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
    active = ("0700.HK", "0005.HK") if market is Market.HK else ("AAPL", "MSFT")
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


def _pool_scenario(
    *, market: Market, kind_index: int, pool: StockPoolStressInput
) -> StockPoolStressScenarioResult:
    """Quantify one pre-registered stock-pool scenario for one market."""
    return quantify_stock_pool_stress(
        _oos_run(),
        stress_config=default_stock_pool_stresses()[kind_index],
        pool=pool,
    )


class SharedOosBootstrapTests(unittest.TestCase):
    """The consolidated bootstrap yields the executed folds every family needs."""

    def test_bootstrap_yields_four_executed_folds(self) -> None:
        oos_run = _oos_run()
        self.assertEqual(len(oos_run.results), 4)
        self.assertTrue(all(result.failure_reason is None for result in oos_run.results))


class CostStressHkUsDistinctTests(unittest.TestCase):
    """Cost stress keeps HK and US costs separate (SP 3.51, 3.60)."""

    def test_config_carries_separate_hk_and_us_configs(self) -> None:
        stress = default_cost_stresses()[0]
        self.assertIsNot(stress.hk, stress.us)
        # HK keeps the board lot (100), US is fractional (1) — never unified.
        self.assertEqual(stress.hk.lot_size, 100)
        self.assertEqual(stress.us.lot_size, 1)
        # Both markets' rates are raised above the documented defaults.
        self.assertGreater(stress.hk.commission_rate, CostConfig().commission_rate)
        self.assertGreater(stress.us.commission_rate, CostConfig().commission_rate)

    def test_hk_and_us_fills_are_both_stressed(self) -> None:
        mixed = _cost_scenario(fills_for=_mixed_fills_for)
        self.assertGreater(mixed.cost_increase, 0.0)
        self.assertGreater(mixed.stressed_costs, mixed.baseline_costs)
        # cost_increase / baseline_final_net_value * 100 is positive (SP 3.51).
        self.assertGreater(mixed.net_value_impact_pct, 0.0)

    def test_cost_increase_is_additive_across_markets(self) -> None:
        hk = _cost_scenario(fills_for=_hk_fills_for)
        us = _cost_scenario(fills_for=_us_fills_for)
        mixed = _cost_scenario(fills_for=_mixed_fills_for)
        # Both markets are re-priced independently: the mixed increase is exactly
        # the sum of the HK-only and US-only increases (neither market is folded
        # into the other).
        self.assertAlmostEqual(mixed.cost_increase, hk.cost_increase + us.cost_increase, delta=1e-6)
        self.assertAlmostEqual(
            mixed.baseline_costs, hk.baseline_costs + us.baseline_costs, delta=1e-6
        )

    def test_each_fill_is_repriced_by_its_own_market_rule(self) -> None:
        stress = default_cost_stresses()[0]
        expected_hk_baseline = hk_order_cost(
            symbol="0001.HK", side=OrderSide.BUY, quantity=1000.0, price=50.0
        ).total_fee
        expected_us_baseline = us_order_cost(
            symbol="AAPL", side=OrderSide.SELL, quantity=10.0, price=100.0
        ).total_cost
        expected_hk_stressed = hk_order_cost(
            symbol="0001.HK",
            side=OrderSide.BUY,
            quantity=1000.0,
            price=50.0,
            config=stress.hk,
        ).total_fee
        expected_us_stressed = us_order_cost(
            symbol="AAPL",
            side=OrderSide.SELL,
            quantity=10.0,
            price=100.0,
            config=stress.us,
        ).total_cost
        mixed = _cost_scenario(fills_for=_mixed_fills_for)
        # The scenario totals span every fold, so each per-fold expectation is
        # multiplied by the fold count.
        fold_count = len(_sequence())
        self.assertAlmostEqual(
            mixed.baseline_costs,
            (expected_hk_baseline + expected_us_baseline) * fold_count,
            delta=1e-6,
        )
        self.assertAlmostEqual(
            mixed.stressed_costs,
            (expected_hk_stressed + expected_us_stressed) * fold_count,
            delta=1e-6,
        )


class LiquidityStressHkUsDistinctTests(unittest.TestCase):
    """Liquidity stress applies the tightened rule to BOTH markets (SP 3.52, 3.60)."""

    def test_both_markets_orders_are_stressed(self) -> None:
        mixed = _liquidity_scenario(
            orders_for=lambda fold: (
                _execution_days(fold, Market.HK) + _execution_days(fold, Market.US)
            ),
            valuations_for=lambda fold: (
                _valuation_days(fold, Market.HK) + _valuation_days(fold, Market.US)
            ),
        )
        self.assertGreater(mixed.unfilled_quantity, 0.0)
        self.assertGreater(mixed.refused_quantity, 0.0)
        self.assertGreater(mixed.warning_count, 0)
        self.assertGreater(mixed.unfilled_order_count, 0)

    def test_mixed_market_aggregates_equal_hk_plus_us(self) -> None:
        hk = _liquidity_scenario(
            orders_for=lambda fold: _execution_days(fold, Market.HK),
            valuations_for=lambda fold: _valuation_days(fold, Market.HK),
        )
        us = _liquidity_scenario(
            orders_for=lambda fold: _execution_days(fold, Market.US),
            valuations_for=lambda fold: _valuation_days(fold, Market.US),
        )
        mixed = _liquidity_scenario(
            orders_for=lambda fold: (
                _execution_days(fold, Market.HK) + _execution_days(fold, Market.US)
            ),
            valuations_for=lambda fold: (
                _valuation_days(fold, Market.HK) + _valuation_days(fold, Market.US)
            ),
        )
        # Neither market is dropped: the mixed-run aggregates are exactly the
        # sum of the HK-only and US-only runs.
        for field in (
            "requested_quantity",
            "filled_quantity",
            "unfilled_quantity",
            "deferred_quantity",
            "cancelled_quantity",
            "refused_quantity",
        ):
            self.assertAlmostEqual(
                getattr(mixed, field),
                getattr(hk, field) + getattr(us, field),
                delta=1e-6,
            )
        self.assertEqual(mixed.refused_count, hk.refused_count + us.refused_count)
        self.assertEqual(mixed.warning_count, hk.warning_count + us.warning_count)

    def test_market_tags_preserved_on_inputs(self) -> None:
        fold = _sequence()[0]
        orders = _execution_days(fold, Market.HK) + _execution_days(fold, Market.US)
        valuations = _valuation_days(fold, Market.HK) + _valuation_days(fold, Market.US)
        self.assertEqual({day.order.market for day in orders}, {Market.HK, Market.US})
        self.assertEqual({day.market for day in valuations}, {Market.HK, Market.US})


class FxStressHkUsDistinctTests(unittest.TestCase):
    """FX stress targets the foreign leg in both base-currency directions (SP 3.53)."""

    def test_hkd_base_stresses_us_fills(self) -> None:
        scenario = _fx_scenario(
            base_currency=Currency.HKD,
            fills_for=lambda fold: (_us_fill_fx(fold),),
            fx_rate_for=_fx_usd_hkd,
        )
        self.assertGreater(scenario.fx_impact, 0.0)
        self.assertGreater(scenario.net_value_impact_pct, 0.0)

    def test_usd_base_stresses_hk_fills(self) -> None:
        scenario = _fx_scenario(
            base_currency=Currency.USD,
            fills_for=lambda fold: (_hk_fill_fx(fold),),
            fx_rate_for=_fx_hkd_usd,
        )
        self.assertGreater(scenario.fx_impact, 0.0)
        self.assertGreater(scenario.net_value_impact_pct, 0.0)

    def test_base_currency_fills_are_not_stressed(self) -> None:
        scenario = _fx_scenario(
            base_currency=Currency.HKD,
            fills_for=lambda fold: (_hk_fill_fx(fold),),
            fx_rate_for=_fx_usd_hkd,
        )
        self.assertEqual(scenario.fx_impact, 0.0)
        self.assertEqual(scenario.refused_count, 0)

    def test_missing_fx_refuses_us_fills_under_hkd_base(self) -> None:
        scenario = _fx_scenario(
            base_currency=Currency.HKD,
            fills_for=lambda fold: (_us_fill_fx(fold),),
            fx_rate_for=_fx_none,
        )
        self.assertGreater(scenario.refused_count, 0)
        refused = scenario.refused_fills[0]
        self.assertIn("refusing to assume 1:1", refused.reason)

    def test_missing_fx_refuses_hk_fills_under_usd_base(self) -> None:
        scenario = _fx_scenario(
            base_currency=Currency.USD,
            fills_for=lambda fold: (_hk_fill_fx(fold),),
            fx_rate_for=_fx_none,
        )
        self.assertGreater(scenario.refused_count, 0)
        refused = scenario.refused_fills[0]
        self.assertIn("refusing to assume 1:1", refused.reason)


class CalendarStressHkUsDistinctTests(unittest.TestCase):
    """Calendar stress keeps each market's calendar distinct (SP 3.54, 3.60)."""

    def test_stress_holiday_closes_hk_but_not_us(self) -> None:
        hk = _calendar_scenario(market=Market.HK, calendar_factory=_calendar_factory(Market.HK))
        # US run fed by the SAME HK-only factory: the injected holiday never
        # reaches US, so its anchor stays a trading day.
        us = _calendar_scenario(market=Market.US, calendar_factory=_calendar_factory(Market.HK))
        self.assertEqual(hk.stress_closed_count, 1)
        self.assertEqual(us.stress_closed_count, 0)
        hk_impact = next(impact for impact in hk.impacts if impact.anchor == _ANCHORS[0])
        us_impact = next(impact for impact in us.impacts if impact.anchor == _ANCHORS[0])
        self.assertTrue(hk_impact.stress_closed)
        self.assertFalse(us_impact.stress_closed)

    def test_market_is_recorded_per_scenario(self) -> None:
        hk = _calendar_scenario(market=Market.HK, calendar_factory=_calendar_factory(Market.HK))
        us = _calendar_scenario(market=Market.US, calendar_factory=_calendar_factory(Market.US))
        self.assertIs(hk.market, Market.HK)
        self.assertIs(us.market, Market.US)

    def test_each_market_can_be_closed_by_its_own_calendar(self) -> None:
        hk = _calendar_scenario(market=Market.HK, calendar_factory=_calendar_factory(Market.HK))
        us = _calendar_scenario(market=Market.US, calendar_factory=_calendar_factory(Market.US))
        self.assertEqual(hk.stress_closed_count, 1)
        self.assertEqual(us.stress_closed_count, 1)


class CorporateActionStressHkUsDistinctTests(unittest.TestCase):
    """Corporate-action stress respects each market's allowed types (SP 3.55, 3.60)."""

    def test_hk_allows_rights_issue(self) -> None:
        scenario = _ca_scenario(
            market=Market.HK,
            events=(_ca_event(Market.HK, action_type=CorporateActionType.RIGHTS_ISSUE),),
        )
        self.assertEqual(scenario.finding_count, 1)
        self.assertTrue(scenario.not_qualified)

    def test_us_skips_rights_issue(self) -> None:
        scenario = _ca_scenario(
            market=Market.US,
            events=(_ca_event(Market.US, action_type=CorporateActionType.RIGHTS_ISSUE),),
        )
        self.assertEqual(scenario.finding_count, 0)
        self.assertIsNone(scenario.conclusion_severity)

    def test_us_allows_split(self) -> None:
        scenario = _ca_scenario(
            market=Market.US,
            events=(_ca_event(Market.US, action_type=CorporateActionType.SPLIT),),
        )
        self.assertEqual(scenario.finding_count, 1)
        self.assertTrue(scenario.not_qualified)

    def test_hk_skips_split(self) -> None:
        scenario = _ca_scenario(
            market=Market.HK,
            events=(_ca_event(Market.HK, action_type=CorporateActionType.SPLIT),),
        )
        self.assertEqual(scenario.finding_count, 0)
        self.assertIsNone(scenario.conclusion_severity)

    def test_allowed_action_type_sets_differ_by_market(self) -> None:
        hk_allowed = allowed_action_types(to_market_target(Market.HK))
        us_allowed = allowed_action_types(to_market_target(Market.US))
        self.assertIn(CorporateActionType.RIGHTS_ISSUE, hk_allowed)
        self.assertNotIn(CorporateActionType.SPLIT, hk_allowed)
        self.assertIn(CorporateActionType.SPLIT, us_allowed)
        self.assertNotIn(CorporateActionType.RIGHTS_ISSUE, us_allowed)


class StockPoolStressHkUsDistinctTests(unittest.TestCase):
    """Stock-pool stress covers both markets' pools (SP 3.56, 3.60)."""

    def test_hk_and_us_pools_are_both_stressed(self) -> None:
        hk = _pool_scenario(market=Market.HK, kind_index=2, pool=_pool(Market.HK))
        us = _pool_scenario(market=Market.US, kind_index=2, pool=_pool(Market.US))
        self.assertLess(hk.coverage_pct, 100.0)
        self.assertLess(us.coverage_pct, 100.0)
        self.assertGreater(hk.impact_pct, 0.0)
        self.assertGreater(us.impact_pct, 0.0)
        self.assertIs(hk.market, Market.HK)
        self.assertIs(us.market, Market.US)

    def test_unknown_history_blocks_both_markets(self) -> None:
        hk = _pool_scenario(
            market=Market.HK,
            kind_index=0,
            pool=_pool(Market.HK, historical_known=False),
        )
        us = _pool_scenario(
            market=Market.US,
            kind_index=0,
            pool=_pool(Market.US, historical_known=False),
        )
        self.assertTrue(hk.blocked)
        self.assertTrue(us.blocked)
        self.assertEqual(hk.conclusion_severity, CoverageSeverity.NOT_QUALIFIED)
        self.assertEqual(us.conclusion_severity, CoverageSeverity.NOT_QUALIFIED)

    def test_market_is_recorded_per_pool(self) -> None:
        for market in (Market.HK, Market.US):
            scenario = _pool_scenario(market=market, kind_index=2, pool=_pool(market))
            self.assertIs(scenario.market, market)


class StressScenarioCoverageTests(unittest.TestCase):
    """The six pre-registered ranges cover all families (SP 3.60)."""

    def test_default_ranges_cover_all_six_families(self) -> None:
        ranges = {
            "cost": default_cost_stresses(),
            "liquidity": default_liquidity_stresses(),
            "fx": default_fx_stresses(),
            "calendar": default_calendar_stresses(),
            "corporate_action": default_corporate_action_stresses(),
            "stock_pool": default_stock_pool_stresses(),
        }
        expected = {
            "cost": 3,
            "liquidity": 3,
            "fx": 5,
            "calendar": 3,
            "corporate_action": 4,
            "stock_pool": 3,
        }
        for family, stresses in ranges.items():
            self.assertEqual(len(stresses), expected[family], family)
            versions = [stress.version for stress in stresses]
            self.assertTrue(all(version for version in versions), family)
            self.assertEqual(len(set(versions)), len(versions), family)

    def test_cost_report_scenarios_register_via_registry(self) -> None:
        """The SP 3.59 registry admits every scenario of the SP 3.51 cost report."""
        oos_run = _oos_run()
        report = compute_cost_stress_report(
            oos_run,
            fills_for=_hk_fills_for,
            net_values_for=_net_values_for,
            base_currency=Currency.HKD,
        )
        refs = scenario_refs_from_reports(StressScenarioCategory.COST, report.scenarios)
        self.assertEqual(
            [ref.scenario_id for ref in refs],
            [stress.version for stress in default_cost_stresses()],
        )
        registrations = tuple(
            build_scenario_registration(
                category=StressScenarioCategory.COST,
                scenario_id=scenario.stress.version,
                market=Market.HK,
                assumptions=("rates scaled by the pre-registered multiplier",),
                parameters={
                    "multiplier": scenario.stress.hk.commission_rate / CostConfig().commission_rate
                },
                dataset_fingerprint=oos_run.dataset_fingerprint,
                code_version=oos_run.code_version,
                baseline_difference=scenario.net_value_impact_pct,
                difference_summary=None,
            )
            for scenario in report.scenarios
        )
        registry = build_stress_registry(version="reg-cost", registrations=registrations)
        require_scenarios_registered(registry, required=refs, conclusion_label="robustness-cost")


if __name__ == "__main__":
    unittest.main()
