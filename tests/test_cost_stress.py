"""Cost and slippage stress tests (MVP 3 / SP 3.51).

Verifies that, within a pre-registered range, raising the HK and US costs,
minimum fees and slippage is quantified as the impact on the OOS net value and
turnover (在预注册范围内提高港美各自成本、最低收费和滑点，量化对 OOS 净值与换手
的影响): the actual OOS fills are re-priced under the stressed SP 2.37 / 2.38
configs, the additional fees reduce the OOS final net value one-for-one, and
the same dollar turnover against the stress-reduced average net value raises
the turnover ratio. Missing FX is never assumed 1:1.
"""

import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from harbor.core.backtest_config import BenchmarkKind, CostConfig
from harbor.core.backtest_domain import Currency, Fill, Market, NetValue, OrderSide
from harbor.core.benchmark import BenchmarkLevel, BenchmarkSeries
from harbor.core.cost_hk import hk_order_cost
from harbor.core.cost_stress import (
    CostStressError,
    CostStressScenarioResult,
    FoldCostStress,
    build_cost_stress_config,
    compute_cost_stress_report,
    cost_stress_config_fingerprint,
    cost_stress_fingerprint,
    cost_stress_json,
    cost_stress_report_fingerprint,
    default_cost_stresses,
    quantify_cost_stress,
)
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.drawdown_events import DrawdownConfig, DrawdownSeries
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.holdout_registry import register_test_set
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
_OOS_START = date(2023, 1, 1)
_OOS_END = date(2026, 12, 30)


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
        NetValue(as_of_date=day, currency=Currency.HKD, cash=1_000_000.0, securities_value=0.0)
        for day in _each_day(fold.test_start, fold.test_end)
    )


def _hk_fill(
    fold,
    *,
    side: OrderSide,
    quantity: float,
    price: float,
    day_offset: int,
    fee: float,
) -> Fill:
    """A deterministic HK fill with an explicit baseline fee."""
    return Fill(
        order_ref=f"o-{fold.fold_index}-{side.value}-{day_offset}",
        symbol="0001.HK",
        market=Market.HK,
        side=side,
        quantity=quantity,
        price=price,
        currency=Currency.HKD,
        trade_date=fold.test_start + timedelta(days=day_offset),
        fee=fee,
    )


def _fills_for(fold) -> tuple[Fill, ...]:
    """Three HK fills per fold: a buy, a sell and a small buy (min-fee stress)."""
    buy = _hk_fill(
        fold,
        side=OrderSide.BUY,
        quantity=1000.0,
        price=50.0,
        day_offset=10,
        fee=hk_order_cost(
            symbol="0001.HK", side=OrderSide.BUY, quantity=1000.0, price=50.0
        ).total_fee,
    )
    sell = _hk_fill(
        fold,
        side=OrderSide.SELL,
        quantity=500.0,
        price=60.0,
        day_offset=20,
        fee=hk_order_cost(
            symbol="0001.HK", side=OrderSide.SELL, quantity=500.0, price=60.0
        ).total_fee,
    )
    small = _hk_fill(
        fold,
        side=OrderSide.BUY,
        quantity=10.0,
        price=1.0,
        day_offset=15,
        fee=hk_order_cost(symbol="0001.HK", side=OrderSide.BUY, quantity=10.0, price=1.0).total_fee,
    )
    return (buy, sell, small)


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


def _scenario(**overrides: object) -> CostStressScenarioResult:
    """Quantify the default 2x stress with overridable arguments."""
    fields: dict[str, object] = {
        "oos_run": _oos_run(),
        "stress_config": default_cost_stresses()[0],
        "fills_for": _fills_for,
        "net_values_for": _net_values_for,
        "base_currency": Currency.HKD,
        "fx_rate_for": None,
    }
    fields.update(overrides)
    return quantify_cost_stress(**fields)  # type: ignore[arg-type]


def _scenario_kwargs() -> dict[str, object]:
    """The validated field values of one quantified scenario, for direct builds."""
    scenario = _scenario()
    return {
        "stress": scenario.stress,
        "folds": scenario.folds,
        "dataset_fingerprint": scenario.dataset_fingerprint,
        "code_version": scenario.code_version,
        "baseline_first_net_value": scenario.baseline_first_net_value,
        "baseline_final_net_value": scenario.baseline_final_net_value,
        "stressed_final_net_value": scenario.stressed_final_net_value,
        "baseline_avg_nav": scenario.baseline_avg_nav,
        "stressed_avg_nav": scenario.stressed_avg_nav,
        "baseline_costs": scenario.baseline_costs,
        "stressed_costs": scenario.stressed_costs,
        "cost_increase": scenario.cost_increase,
        "baseline_cumulative_return": scenario.baseline_cumulative_return,
        "stressed_cumulative_return": scenario.stressed_cumulative_return,
        "net_value_impact_pct": scenario.net_value_impact_pct,
        "baseline_turnover": scenario.baseline_turnover,
        "stressed_turnover": scenario.stressed_turnover,
        "turnover_delta": scenario.turnover_delta,
        "fingerprint": "x" * 64,
    }


def _scenario_direct(**overrides: object) -> CostStressScenarioResult:
    """A directly-constructed scenario (bypasses quantify) with overrides."""
    fields = _scenario_kwargs()
    fields.update(overrides)
    return CostStressScenarioResult(**fields)  # type: ignore[arg-type]


class CostStressErrorTests(unittest.TestCase):
    """The dedicated error type."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(CostStressError, ValueError))

    def test_lowering_a_default_rejected(self) -> None:
        with self.assertRaises(CostStressError):
            build_cost_stress_config(
                version="v1",
                hk=CostConfig(commission_rate=0.0001),
                us=CostConfig(),
            )


class CostStressConfigTests(unittest.TestCase):
    """The pre-registered stressed cost configuration."""

    def test_valid_config(self) -> None:
        stress = default_cost_stresses()[0]
        self.assertEqual(stress.version, "cost-stress-2x")
        self.assertEqual(stress.source, "pre-registered")
        self.assertEqual(stress.hk.commission_rate, 0.001)
        self.assertEqual(stress.us.slippage_bps, 10.0)

    def test_fingerprint_rederivable(self) -> None:
        stress = default_cost_stresses()[0]
        self.assertEqual(stress.fingerprint, cost_stress_config_fingerprint(stress))

    def test_fingerprint_stable(self) -> None:
        self.assertEqual(
            default_cost_stresses()[0].fingerprint,
            default_cost_stresses()[0].fingerprint,
        )

    def test_empty_version_rejected(self) -> None:
        with self.assertRaises(CostStressError):
            build_cost_stress_config(version="", hk=CostConfig(), us=CostConfig())

    def test_empty_source_rejected(self) -> None:
        with self.assertRaises(CostStressError):
            build_cost_stress_config(version="v1", source="", hk=CostConfig(), us=CostConfig())

    def test_hk_stamp_below_default_rejected(self) -> None:
        with self.assertRaises(CostStressError):
            build_cost_stress_config(
                version="v1",
                hk=CostConfig(stamp_duty_rate=0.0005),
                us=CostConfig(),
            )

    def test_us_regulatory_below_default_rejected(self) -> None:
        with self.assertRaises(CostStressError):
            build_cost_stress_config(
                version="v1",
                hk=CostConfig(),
                us=CostConfig(regulatory_fee_rate=0.00001),
            )

    def test_default_stresses_increasing(self) -> None:
        stresses = default_cost_stresses()
        self.assertEqual(len(stresses), 3)
        versions = [stress.version for stress in stresses]
        self.assertEqual(versions, ["cost-stress-2x", "cost-stress-5x", "cost-stress-10x"])
        self.assertLess(stresses[0].hk.commission_rate, stresses[1].hk.commission_rate)
        self.assertLess(stresses[1].hk.commission_rate, stresses[2].hk.commission_rate)

    def test_readable(self) -> None:
        self.assertIn("cost-stress-2x", default_cost_stresses()[0].readable())


class FoldCostStressTests(unittest.TestCase):
    """One fold's re-priced cost impact."""

    def test_valid_executed_fold(self) -> None:
        fold = FoldCostStress(
            fold_index=0,
            executed=True,
            baseline_costs=100.0,
            stressed_costs=150.0,
            cost_increase=50.0,
            baseline_final_net_value=1_000_000.0,
            stressed_final_net_value=999_950.0,
            turnover=0.1,
            failure_reason=None,
        )
        self.assertEqual(fold.cost_increase, 50.0)

    def test_valid_non_executed_fold(self) -> None:
        fold = FoldCostStress(
            fold_index=1,
            executed=False,
            baseline_costs=None,
            stressed_costs=None,
            cost_increase=None,
            baseline_final_net_value=None,
            stressed_final_net_value=None,
            turnover=None,
            failure_reason="denied",
        )
        self.assertFalse(fold.executed)

    def test_executed_with_failure_reason_rejected(self) -> None:
        with self.assertRaises(CostStressError):
            FoldCostStress(
                fold_index=0,
                executed=True,
                baseline_costs=100.0,
                stressed_costs=150.0,
                cost_increase=50.0,
                baseline_final_net_value=1_000_000.0,
                stressed_final_net_value=999_950.0,
                turnover=None,
                failure_reason="boom",
            )

    def test_non_executed_without_reason_rejected(self) -> None:
        with self.assertRaises(CostStressError):
            FoldCostStress(
                fold_index=1,
                executed=False,
                baseline_costs=None,
                stressed_costs=None,
                cost_increase=None,
                baseline_final_net_value=None,
                stressed_final_net_value=None,
                turnover=None,
                failure_reason=None,
            )

    def test_stressed_final_inconsistent_rejected(self) -> None:
        with self.assertRaises(CostStressError):
            FoldCostStress(
                fold_index=0,
                executed=True,
                baseline_costs=100.0,
                stressed_costs=150.0,
                cost_increase=50.0,
                baseline_final_net_value=1_000_000.0,
                stressed_final_net_value=999_000.0,
                turnover=None,
                failure_reason=None,
            )

    def test_readable(self) -> None:
        fold = FoldCostStress(
            fold_index=0,
            executed=True,
            baseline_costs=100.0,
            stressed_costs=150.0,
            cost_increase=50.0,
            baseline_final_net_value=1_000_000.0,
            stressed_final_net_value=999_950.0,
            turnover=None,
            failure_reason=None,
        )
        self.assertIn("+50.0", fold.readable())


class CostStressScenarioResultTests(unittest.TestCase):
    """The quantified scenario and its consistency invariants."""

    def test_valid_scenario(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.executed_count, 4)

    def test_empty_folds_rejected(self) -> None:
        with self.assertRaises(CostStressError):
            _scenario_direct(folds=())

    def test_non_sequential_folds_rejected(self) -> None:
        folds = tuple(replace(f, fold_index=f.fold_index + 1) for f in _scenario().folds)
        with self.assertRaises(CostStressError):
            _scenario_direct(folds=folds)

    def test_no_executed_fold_rejected(self) -> None:
        scenario = _scenario()
        non_executed = tuple(
            FoldCostStress(
                fold_index=index,
                executed=False,
                baseline_costs=None,
                stressed_costs=None,
                cost_increase=None,
                baseline_final_net_value=None,
                stressed_final_net_value=None,
                turnover=None,
                failure_reason="denied",
            )
            for index in range(len(scenario.folds))
        )
        with self.assertRaises(CostStressError):
            _scenario_direct(folds=non_executed)

    def test_baseline_costs_sum_inconsistent_rejected(self) -> None:
        with self.assertRaises(CostStressError):
            _scenario_direct(baseline_costs=999.0)

    def test_len_iter_getitem(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario[0].fold_index, 0)
        self.assertEqual([fold.fold_index for fold in scenario], [0, 1, 2, 3])

    def test_readable(self) -> None:
        self.assertIn("cost-stress-2x", _scenario().readable())


class QuantifyCostStressTests(unittest.TestCase):
    """The impact quantification on the OOS net value and turnover."""

    def test_every_fold_priced(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.executed_count, 4)
        self.assertTrue(all(fold.executed for fold in scenario.folds))

    def test_baseline_costs_equal_fill_fees(self) -> None:
        scenario = _scenario()
        expected = sum(
            sum(fill.fee for fill in _fills_for(result.validation.fold))
            for result in _oos_run().results
            if result.executed
        )
        self.assertAlmostEqual(scenario.baseline_costs, expected, places=2)

    def test_stressed_costs_raise(self) -> None:
        scenario = _scenario()
        self.assertGreater(scenario.stressed_costs, scenario.baseline_costs)
        self.assertAlmostEqual(
            scenario.cost_increase,
            scenario.stressed_costs - scenario.baseline_costs,
            places=2,
        )

    def test_min_fee_stress_bites_small_fill(self) -> None:
        # the small buy (notional 10) pays the pre-registered minimum commission.
        stress = default_cost_stresses()[0]
        small_baseline = hk_order_cost(
            symbol="0001.HK", side=OrderSide.BUY, quantity=10.0, price=1.0
        ).total_fee
        small_stressed = hk_order_cost(
            symbol="0001.HK", side=OrderSide.BUY, quantity=10.0, price=1.0, config=stress.hk
        ).total_fee
        self.assertGreater(small_stressed - small_baseline, 9.0)

    def test_per_fold_records(self) -> None:
        scenario = _scenario()
        for fold in scenario.folds:
            self.assertTrue(fold.executed)
            self.assertAlmostEqual(fold.baseline_final_net_value, 1_000_000.0, places=6)
            self.assertLess(fold.stressed_final_net_value, fold.baseline_final_net_value)
            self.assertGreater(fold.cost_increase, 0.0)

    def test_net_value_impact(self) -> None:
        scenario = _scenario()
        self.assertAlmostEqual(scenario.baseline_final_net_value, 1_000_000.0, places=6)
        self.assertAlmostEqual(
            scenario.stressed_final_net_value,
            scenario.baseline_final_net_value - scenario.cost_increase,
            places=2,
        )
        self.assertAlmostEqual(
            scenario.net_value_impact_pct,
            scenario.cost_increase / scenario.baseline_final_net_value * 100.0,
            places=4,
        )
        self.assertGreater(scenario.net_value_impact_pct, 0.0)

    def test_cumulative_returns(self) -> None:
        scenario = _scenario()
        self.assertAlmostEqual(scenario.baseline_cumulative_return, 0.0, places=6)
        self.assertLess(scenario.stressed_cumulative_return, 0.0)
        self.assertAlmostEqual(
            scenario.stressed_cumulative_return,
            -scenario.cost_increase / scenario.baseline_first_net_value,
            places=6,
        )

    def test_turnover_impact(self) -> None:
        scenario = _scenario()
        self.assertAlmostEqual(scenario.baseline_turnover, 0.12, places=4)
        self.assertLess(scenario.stressed_avg_nav, scenario.baseline_avg_nav)
        self.assertIsNotNone(scenario.stressed_turnover)
        assert scenario.stressed_turnover is not None
        self.assertGreater(scenario.stressed_turnover, scenario.baseline_turnover)
        self.assertGreater(scenario.turnover_delta, 0.0)

    def test_higher_stress_larger_impact(self) -> None:
        two_x = _scenario()
        ten_x = _scenario(stress_config=default_cost_stresses()[2])
        self.assertGreater(ten_x.cost_increase, two_x.cost_increase)
        self.assertGreater(ten_x.net_value_impact_pct, two_x.net_value_impact_pct)

    def test_cross_market_turnover_with_fx(self) -> None:
        def us_fills_for(fold) -> tuple[Fill, ...]:
            return (
                Fill(
                    order_ref=f"us-{fold.fold_index}",
                    symbol="AAPL",
                    market=Market.US,
                    side=OrderSide.BUY,
                    quantity=10.0,
                    price=100.0,
                    currency=Currency.USD,
                    trade_date=fold.test_start + timedelta(days=12),
                    fee=1.0,
                ),
                Fill(
                    order_ref=f"us-{fold.fold_index}-s",
                    symbol="AAPL",
                    market=Market.US,
                    side=OrderSide.SELL,
                    quantity=5.0,
                    price=110.0,
                    currency=Currency.USD,
                    trade_date=fold.test_start + timedelta(days=22),
                    fee=1.0,
                ),
            )

        def fx_rate(from_currency, to_currency, as_of):
            return 7.8

        scenario = _scenario(
            fills_for=us_fills_for,
            fx_rate_for=fx_rate,
        )
        # 4 folds x 2 US fills: buy 10@100, sell 5@110, fx 7.8 -> min base 17,160
        self.assertAlmostEqual(scenario.baseline_turnover, 0.01716, places=5)
        self.assertIsNotNone(scenario.stressed_turnover)
        assert scenario.stressed_turnover is not None
        self.assertGreater(scenario.stressed_turnover, scenario.baseline_turnover)

    def test_missing_fx_turnover_unavailable(self) -> None:
        def us_fills_for(fold) -> tuple[Fill, ...]:
            return (
                Fill(
                    order_ref=f"us-{fold.fold_index}",
                    symbol="AAPL",
                    market=Market.US,
                    side=OrderSide.BUY,
                    quantity=10.0,
                    price=100.0,
                    currency=Currency.USD,
                    trade_date=fold.test_start + timedelta(days=12),
                    fee=1.0,
                ),
            )

        scenario = _scenario(fills_for=us_fills_for, fx_rate_for=None)
        self.assertIsNone(scenario.baseline_turnover)
        self.assertIsNone(scenario.stressed_turnover)
        self.assertIsNone(scenario.turnover_delta)
        # costs are still quantified (missing FX never blocks the cost impact)
        self.assertGreater(scenario.cost_increase, 0.0)

    def test_no_executed_fold_rejected(self) -> None:
        with self.assertRaises(CostStressError):
            _scenario(oos_run=_oos_run(current_stage=ValidationStatus.TUNING))


class ReportTests(unittest.TestCase):
    """The quantified cost stress across the pre-registered range."""

    def test_report_has_three_scenarios(self) -> None:
        report = compute_cost_stress_report(
            _oos_run(),
            fills_for=_fills_for,
            net_values_for=_net_values_for,
            base_currency=Currency.HKD,
        )
        self.assertEqual(len(report), 3)

    def test_scenario_lookup(self) -> None:
        report = compute_cost_stress_report(
            _oos_run(),
            fills_for=_fills_for,
            net_values_for=_net_values_for,
            base_currency=Currency.HKD,
        )
        self.assertIsNotNone(report.scenario("cost-stress-2x"))
        self.assertIsNone(report.scenario("nope"))

    def test_ten_x_largest_impact(self) -> None:
        report = compute_cost_stress_report(
            _oos_run(),
            fills_for=_fills_for,
            net_values_for=_net_values_for,
            base_currency=Currency.HKD,
        )
        ten_x = report.scenario("cost-stress-10x")
        two_x = report.scenario("cost-stress-2x")
        assert ten_x is not None
        assert two_x is not None
        self.assertGreater(ten_x.cost_increase, two_x.cost_increase)

    def test_report_context(self) -> None:
        report = compute_cost_stress_report(
            _oos_run(),
            fills_for=_fills_for,
            net_values_for=_net_values_for,
            base_currency=Currency.HKD,
        )
        self.assertEqual(report.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(report.code_version, "1.0.0")
        self.assertTrue(all(s.dataset_fingerprint == _FINGERPRINT for s in report))

    def test_readable(self) -> None:
        report = compute_cost_stress_report(
            _oos_run(),
            fills_for=_fills_for,
            net_values_for=_net_values_for,
            base_currency=Currency.HKD,
        )
        self.assertIn("3 cost stress scenario(s)", report.readable())


class FingerprintTests(unittest.TestCase):
    """Stable, re-derivable, sensitive cost-stress fingerprints."""

    def test_config_sha256(self) -> None:
        digest = default_cost_stresses()[0].fingerprint
        self.assertEqual(len(digest), 64)
        int(digest, 16)

    def test_config_rederivable(self) -> None:
        stress = default_cost_stresses()[0]
        self.assertEqual(stress.fingerprint, cost_stress_config_fingerprint(stress))

    def test_config_changes_with_parameter(self) -> None:
        base = default_cost_stresses()[0]
        other = build_cost_stress_config(
            version=base.version,
            source=base.source,
            hk=base.hk,
            us=base.us.model_copy(update={"slippage_bps": 15.0}),
        )
        self.assertNotEqual(base.fingerprint, other.fingerprint)

    def test_scenario_sha256_rederivable(self) -> None:
        scenario = _scenario()
        self.assertEqual(len(scenario.fingerprint), 64)
        int(scenario.fingerprint, 16)
        self.assertEqual(scenario.fingerprint, cost_stress_fingerprint(scenario))

    def test_scenario_stable(self) -> None:
        self.assertEqual(_scenario().fingerprint, _scenario().fingerprint)

    def test_scenario_changes_with_stress(self) -> None:
        self.assertNotEqual(
            _scenario().fingerprint,
            _scenario(stress_config=default_cost_stresses()[1]).fingerprint,
        )

    def test_scenario_changes_with_fills(self) -> None:
        def fewer_fills_for(fold) -> tuple[Fill, ...]:
            return _fills_for(fold)[:1]

        self.assertNotEqual(
            _scenario().fingerprint,
            _scenario(fills_for=fewer_fills_for).fingerprint,
        )

    def test_json_excludes_fingerprint_and_key_sorted(self) -> None:
        scenario = _scenario()
        serialized = cost_stress_json(scenario)
        self.assertNotIn('"fingerprint"', serialized)
        self.assertIn('"version":"cost-stress-2x"', serialized)
        self.assertIn('"dataset_fingerprint":"' + _FINGERPRINT + '"', serialized)
        payload = json.loads(serialized)
        self.assertEqual(
            serialized,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )

    def test_report_fingerprint_rederivable(self) -> None:
        report = compute_cost_stress_report(
            _oos_run(),
            fills_for=_fills_for,
            net_values_for=_net_values_for,
            base_currency=Currency.HKD,
        )
        self.assertEqual(len(report.fingerprint), 64)
        self.assertEqual(report.fingerprint, cost_stress_report_fingerprint(report))
