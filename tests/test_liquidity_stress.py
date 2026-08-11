"""Liquidity and execution stress tests (MVP 3 / SP 3.52).

Verifies that, within a pre-registered range, tightening the participation
rate, suspension, missing-price and deferred-fill assumptions is quantified on
the OOS execution, and that the unfilled orders (未成交订单) and valuation
warnings (估值告警) are PRESERVED rather than dropped (收紧成交参与率、停牌、缺
价和延期成交假设，保留未成交订单和估值告警).
"""

import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from harbor.core.backtest_config import BenchmarkKind, UnfilledPolicy
from harbor.core.backtest_domain import Currency, Market, Order, OrderSide
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.benchmark import BenchmarkLevel, BenchmarkSeries
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.drawdown_events import DrawdownConfig, DrawdownSeries
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.holdout_registry import register_test_set
from harbor.core.liquidity_stress import (
    ExecutionDay,
    FoldLiquidityStress,
    LiquidityStressError,
    LiquidityStressScenarioResult,
    ValuationDay,
    build_liquidity_stress_config,
    compute_liquidity_stress_report,
    default_liquidity_stresses,
    liquidity_stress_config_fingerprint,
    liquidity_stress_fingerprint,
    liquidity_stress_json,
    liquidity_stress_report_fingerprint,
    quantify_liquidity_stress,
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
from harbor.core.suspension import RefusedOrder, SuspensionWarning
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
from harbor.core.volume_limit import VolumeLimitOutcome

_FINGERPRINT = "f" * 64
_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_OOS_START = date(2023, 1, 1)
_OOS_END = date(2026, 12, 30)


def _order(fold, *, quantity: float, day_offset: int, ref: str = "o") -> Order:
    """A deterministic HK buy order on a day inside the fold."""
    return Order(
        symbol="0001.HK",
        market=Market.HK,
        side=OrderSide.BUY,
        quantity=quantity,
        currency=Currency.HKD,
        trade_date=fold.test_start + timedelta(days=day_offset),
        ref=f"{ref}-{fold.fold_index}",
    )


def _quote(fold, day_offset: int, *, volume: int = 100000, close: float = 50.0) -> DailyQuote:
    """A deterministic HK quote on a day inside the fold."""
    day = fold.test_start + timedelta(days=day_offset)
    return DailyQuote(
        market=Market.HK,
        symbol="0001.HK",
        day=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        adjusted_close=close,
    )


def _execution_days(fold) -> tuple[ExecutionDay, ...]:
    """Three orders per fold: a participation-capped buy, a full buy, a refusal."""
    big = ExecutionDay(
        order=_order(fold, quantity=1000.0, day_offset=10, ref="big"),
        quote=_quote(fold, 10, volume=10000),
        volume=10000,
        reference_price=50.0,
    )
    full = ExecutionDay(
        order=_order(fold, quantity=100.0, day_offset=12, ref="full"),
        quote=_quote(fold, 12, volume=100000),
        volume=100000,
        reference_price=50.0,
    )
    suspended = ExecutionDay(
        order=_order(fold, quantity=200.0, day_offset=14, ref="susp"),
        quote=None,
        volume=0,
        reference_price=50.0,
    )
    return (big, full, suspended)


def _valuation_days(fold) -> tuple[ValuationDay, ...]:
    """Two valuations: one carried-forward (warning), one quoted (no warning)."""
    missing_day = fold.test_start + timedelta(days=16)
    return (
        ValuationDay(
            market=Market.HK,
            symbol="0001.HK",
            day=missing_day,
            quote=None,
            last_quote=_quote(fold, 15, volume=5000, close=49.5),
        ),
        ValuationDay(
            market=Market.HK,
            symbol="0001.HK",
            day=fold.test_start + timedelta(days=17),
            quote=_quote(fold, 17, volume=5000),
            last_quote=None,
        ),
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


def _scenario(**overrides: object) -> LiquidityStressScenarioResult:
    """Quantify the default tight stress with overridable arguments."""
    fields: dict[str, object] = {
        "oos_run": _oos_run(),
        "stress_config": default_liquidity_stresses()[0],
        "orders_for": _execution_days,
        "valuations_for": _valuation_days,
    }
    fields.update(overrides)
    return quantify_liquidity_stress(**fields)  # type: ignore[arg-type]


def _fold_kwargs(**overrides: object) -> dict[str, object]:
    """The validated field values of one executed fold record, for direct builds."""
    fields: dict[str, object] = {
        "fold_index": 0,
        "executed": True,
        "requested_quantity": 1300.0,
        "filled_quantity": 600.0,
        "unfilled_quantity": 700.0,
        "deferred_quantity": 500.0,
        "cancelled_quantity": 0.0,
        "refused_quantity": 200.0,
        "refused_orders": (),
        "unfilled_orders": (),
        "valuation_warnings": (),
        "failure_reason": None,
    }
    fields.update(overrides)
    return fields


def _fold_direct(**overrides: object) -> FoldLiquidityStress:
    """A directly-constructed executed fold record with overrides."""
    return FoldLiquidityStress(**_fold_kwargs(**overrides))  # type: ignore[arg-type]


class LiquidityStressErrorTests(unittest.TestCase):
    """The dedicated error type."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(LiquidityStressError, ValueError))

    def test_loosened_participation_rejected(self) -> None:
        with self.assertRaises(LiquidityStressError):
            build_liquidity_stress_config(
                version="v1",
                participation_rate=0.15,
                on_unfilled=UnfilledPolicy.DEFER,
            )


class LiquidityStressConfigTests(unittest.TestCase):
    """The pre-registered stressed execution assumption set."""

    def test_valid_config(self) -> None:
        stress = default_liquidity_stresses()[0]
        self.assertEqual(stress.version, "liquidity-stress-tight")
        self.assertEqual(stress.source, "pre-registered")
        self.assertEqual(stress.participation_rate, 0.05)
        self.assertIs(stress.on_unfilled, UnfilledPolicy.DEFER)
        self.assertTrue(stress.suspension.warn)

    def test_default_range(self) -> None:
        stresses = default_liquidity_stresses()
        self.assertEqual(len(stresses), 3)
        self.assertEqual(
            [s.version for s in stresses],
            ["liquidity-stress-tight", "liquidity-stress-thin", "liquidity-stress-severe"],
        )
        self.assertLess(stresses[1].participation_rate, stresses[0].participation_rate)
        self.assertLess(stresses[2].participation_rate, stresses[1].participation_rate)
        self.assertIs(stresses[2].on_unfilled, UnfilledPolicy.CANCEL)

    def test_fingerprint_rederivable_stable(self) -> None:
        stress = default_liquidity_stresses()[0]
        self.assertEqual(stress.fingerprint, liquidity_stress_config_fingerprint(stress))
        self.assertEqual(stress.fingerprint, default_liquidity_stresses()[0].fingerprint)

    def test_empty_version_rejected(self) -> None:
        with self.assertRaises(LiquidityStressError):
            build_liquidity_stress_config(
                version="", participation_rate=0.05, on_unfilled=UnfilledPolicy.DEFER
            )

    def test_empty_source_rejected(self) -> None:
        with self.assertRaises(LiquidityStressError):
            build_liquidity_stress_config(
                version="v1", source="", participation_rate=0.05, on_unfilled=UnfilledPolicy.DEFER
            )

    def test_zero_participation_rejected(self) -> None:
        with self.assertRaises(LiquidityStressError):
            build_liquidity_stress_config(
                version="v1", participation_rate=0.0, on_unfilled=UnfilledPolicy.DEFER
            )

    def test_readable(self) -> None:
        self.assertIn("liquidity-stress-tight", default_liquidity_stresses()[0].readable())


class ExecutionInputTests(unittest.TestCase):
    """The injected execution and valuation contexts."""

    def test_negative_volume_rejected(self) -> None:
        with self.assertRaises(LiquidityStressError):
            ExecutionDay(
                order=_order(_sequence()[0], quantity=10.0, day_offset=1),
                quote=None,
                volume=-1,
                reference_price=50.0,
            )

    def test_non_positive_reference_price_rejected(self) -> None:
        with self.assertRaises(LiquidityStressError):
            ExecutionDay(
                order=_order(_sequence()[0], quantity=10.0, day_offset=1),
                quote=None,
                volume=100,
                reference_price=0.0,
            )

    def test_empty_valuation_symbol_rejected(self) -> None:
        with self.assertRaises(LiquidityStressError):
            ValuationDay(
                market=Market.HK, symbol="", day=date(2023, 1, 1), quote=None, last_quote=None
            )


class FoldLiquidityStressTests(unittest.TestCase):
    """One fold's stressed execution outcome."""

    def test_valid_executed_fold(self) -> None:
        fold = _fold_direct()
        self.assertTrue(fold.executed)
        self.assertAlmostEqual(fold.fill_rate, 600.0 / 1300.0, places=6)

    def test_valid_non_executed_fold(self) -> None:
        fold = FoldLiquidityStress(
            fold_index=1,
            executed=False,
            requested_quantity=0.0,
            filled_quantity=0.0,
            unfilled_quantity=0.0,
            deferred_quantity=0.0,
            cancelled_quantity=0.0,
            refused_quantity=0.0,
            refused_orders=(),
            unfilled_orders=(),
            valuation_warnings=(),
            failure_reason="denied",
        )
        self.assertFalse(fold.executed)

    def test_non_executed_with_quantity_rejected(self) -> None:
        with self.assertRaises(LiquidityStressError):
            FoldLiquidityStress(
                fold_index=1,
                executed=False,
                requested_quantity=100.0,
                filled_quantity=0.0,
                unfilled_quantity=0.0,
                deferred_quantity=0.0,
                cancelled_quantity=0.0,
                refused_quantity=0.0,
                refused_orders=(),
                unfilled_orders=(),
                valuation_warnings=(),
                failure_reason="denied",
            )

    def test_unfilled_inconsistent_rejected(self) -> None:
        with self.assertRaises(LiquidityStressError):
            _fold_direct(unfilled_quantity=999.0)

    def test_unfilled_not_decomposed_rejected(self) -> None:
        with self.assertRaises(LiquidityStressError):
            _fold_direct(refused_quantity=100.0)

    def test_readable(self) -> None:
        self.assertIn("filled 600.00/1300.00", _fold_direct().readable())


class LiquidityStressScenarioResultTests(unittest.TestCase):
    """The quantified scenario and its consistency invariants."""

    def test_valid_scenario(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.executed_count, 4)

    def test_empty_folds_rejected(self) -> None:
        scenario = _scenario()
        with self.assertRaises(LiquidityStressError):
            LiquidityStressScenarioResult(
                stress=scenario.stress,
                folds=(),
                dataset_fingerprint=scenario.dataset_fingerprint,
                code_version=scenario.code_version,
                requested_quantity=scenario.requested_quantity,
                filled_quantity=scenario.filled_quantity,
                unfilled_quantity=scenario.unfilled_quantity,
                deferred_quantity=scenario.deferred_quantity,
                cancelled_quantity=scenario.cancelled_quantity,
                refused_quantity=scenario.refused_quantity,
                refused_count=scenario.refused_count,
                unfilled_order_count=scenario.unfilled_order_count,
                warning_count=scenario.warning_count,
                fingerprint="x" * 64,
            )

    def test_no_executed_fold_rejected(self) -> None:
        scenario = _scenario()
        non_executed = tuple(
            FoldLiquidityStress(
                fold_index=index,
                executed=False,
                requested_quantity=0.0,
                filled_quantity=0.0,
                unfilled_quantity=0.0,
                deferred_quantity=0.0,
                cancelled_quantity=0.0,
                refused_quantity=0.0,
                refused_orders=(),
                unfilled_orders=(),
                valuation_warnings=(),
                failure_reason="denied",
            )
            for index in range(len(scenario.folds))
        )
        with self.assertRaises(LiquidityStressError):
            LiquidityStressScenarioResult(
                stress=scenario.stress,
                folds=non_executed,
                dataset_fingerprint=scenario.dataset_fingerprint,
                code_version=scenario.code_version,
                requested_quantity=0.0,
                filled_quantity=0.0,
                unfilled_quantity=0.0,
                deferred_quantity=0.0,
                cancelled_quantity=0.0,
                refused_quantity=0.0,
                refused_count=0,
                unfilled_order_count=0,
                warning_count=0,
                fingerprint="x" * 64,
            )

    def test_sum_inconsistent_rejected(self) -> None:
        scenario = _scenario()
        with self.assertRaises(LiquidityStressError):
            LiquidityStressScenarioResult(
                stress=scenario.stress,
                folds=scenario.folds,
                dataset_fingerprint=scenario.dataset_fingerprint,
                code_version=scenario.code_version,
                requested_quantity=scenario.requested_quantity,
                filled_quantity=scenario.filled_quantity,
                unfilled_quantity=scenario.unfilled_quantity,
                deferred_quantity=scenario.deferred_quantity,
                cancelled_quantity=scenario.cancelled_quantity,
                refused_quantity=scenario.refused_quantity,
                refused_count=99,
                unfilled_order_count=scenario.unfilled_order_count,
                warning_count=scenario.warning_count,
                fingerprint="x" * 64,
            )

    def test_len_iter_getitem(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario[0].fold_index, 0)
        self.assertEqual([fold.fold_index for fold in scenario], [0, 1, 2, 3])

    def test_readable(self) -> None:
        self.assertIn("liquidity-stress-tight", _scenario().readable())


class QuantifyLiquidityStressTests(unittest.TestCase):
    """The tightened assumptions quantified on the OOS folds."""

    def test_every_fold_stressed(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.executed_count, 4)
        self.assertTrue(all(fold.executed for fold in scenario.folds))

    def test_tight_fill_rate(self) -> None:
        scenario = _scenario()
        self.assertAlmostEqual(scenario.fill_rate, 2400.0 / 5200.0, places=6)
        self.assertAlmostEqual(scenario.requested_quantity, 5200.0, places=2)
        self.assertAlmostEqual(scenario.filled_quantity, 2400.0, places=2)

    def test_per_fold_records(self) -> None:
        scenario = _scenario()
        for fold in scenario.folds:
            self.assertAlmostEqual(fold.requested_quantity, 1300.0, places=2)
            self.assertAlmostEqual(fold.filled_quantity, 600.0, places=2)
            self.assertAlmostEqual(fold.unfilled_quantity, 700.0, places=2)

    def test_refused_orders_preserved(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.refused_count, 4)
        self.assertEqual(len(scenario.refused_orders), 4)
        for refusal in scenario.refused_orders:
            self.assertIsInstance(refusal, RefusedOrder)
            self.assertIn("no quote on", refusal.reason)

    def test_unfilled_orders_preserved(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.unfilled_order_count, 4)
        self.assertEqual(len(scenario.unfilled_orders), 4)
        for outcome in scenario.unfilled_orders:
            self.assertIsInstance(outcome, VolumeLimitOutcome)
            self.assertAlmostEqual(outcome.unfilled_quantity, 500.0, places=2)
            self.assertIs(outcome.policy, UnfilledPolicy.DEFER)

    def test_valuation_warnings_preserved(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.warning_count, 4)
        self.assertEqual(len(scenario.valuation_warnings), 4)
        for warning in scenario.valuation_warnings:
            self.assertIsInstance(warning, SuspensionWarning)
            self.assertIn("last available close", warning.message)

    def test_deferred_vs_cancelled(self) -> None:
        tight = _scenario()
        self.assertAlmostEqual(tight.deferred_quantity, 2000.0, places=2)
        self.assertAlmostEqual(tight.cancelled_quantity, 0.0, places=2)
        severe = _scenario(stress_config=default_liquidity_stresses()[2])
        self.assertAlmostEqual(severe.cancelled_quantity, 3600.0, places=2)
        self.assertAlmostEqual(severe.deferred_quantity, 0.0, places=2)

    def test_tightening_reduces_fill_rate(self) -> None:
        tight = _scenario()
        thin = _scenario(stress_config=default_liquidity_stresses()[1])
        severe = _scenario(stress_config=default_liquidity_stresses()[2])
        self.assertLess(thin.fill_rate, tight.fill_rate)
        self.assertLess(severe.fill_rate, thin.fill_rate)

    def test_full_order_always_fills(self) -> None:
        # the 100-share order against 100,000 volume is never capped.
        scenario = _scenario()
        self.assertGreater(scenario.filled_quantity, 4 * 600.0 - 1.0)

    def test_no_orders_fill_rate_none(self) -> None:
        scenario = _scenario(
            orders_for=lambda fold: (),
            valuations_for=lambda fold: (),
        )
        self.assertEqual(scenario.requested_quantity, 0.0)
        self.assertIsNone(scenario.fill_rate)
        self.assertEqual(scenario.refused_count, 0)
        self.assertEqual(scenario.warning_count, 0)

    def test_no_executed_fold_rejected(self) -> None:
        with self.assertRaises(LiquidityStressError):
            _scenario(oos_run=_oos_run(current_stage=ValidationStatus.TUNING))

    def test_missing_price_without_last_quote_raises(self) -> None:
        def bad_valuations(fold) -> tuple[ValuationDay, ...]:
            return (
                ValuationDay(
                    market=Market.HK,
                    symbol="0001.HK",
                    day=fold.test_start + timedelta(days=16),
                    quote=None,
                    last_quote=None,
                ),
            )

        with self.assertRaises(ValueError):
            _scenario(valuations_for=bad_valuations)


class ReportTests(unittest.TestCase):
    """The stressed outcomes across the pre-registered range."""

    def test_report_has_three_scenarios(self) -> None:
        report = compute_liquidity_stress_report(
            _oos_run(),
            orders_for=_execution_days,
            valuations_for=_valuation_days,
        )
        self.assertEqual(len(report), 3)

    def test_scenario_lookup(self) -> None:
        report = compute_liquidity_stress_report(
            _oos_run(),
            orders_for=_execution_days,
            valuations_for=_valuation_days,
        )
        self.assertIsNotNone(report.scenario("liquidity-stress-tight"))
        self.assertIsNone(report.scenario("nope"))

    def test_tighter_stress_lower_fill_rate(self) -> None:
        report = compute_liquidity_stress_report(
            _oos_run(),
            orders_for=_execution_days,
            valuations_for=_valuation_days,
        )
        rates = [scenario.fill_rate for scenario in report]
        self.assertEqual(rates, sorted(rates, reverse=True))

    def test_report_context(self) -> None:
        report = compute_liquidity_stress_report(
            _oos_run(),
            orders_for=_execution_days,
            valuations_for=_valuation_days,
        )
        self.assertEqual(report.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(report.code_version, "1.0.0")

    def test_readable(self) -> None:
        report = compute_liquidity_stress_report(
            _oos_run(),
            orders_for=_execution_days,
            valuations_for=_valuation_days,
        )
        self.assertIn("3 liquidity stress scenario(s)", report.readable())


class FingerprintTests(unittest.TestCase):
    """Stable, re-derivable, sensitive liquidity-stress fingerprints."""

    def test_config_sha256_rederivable(self) -> None:
        stress = default_liquidity_stresses()[0]
        self.assertEqual(len(stress.fingerprint), 64)
        int(stress.fingerprint, 16)
        self.assertEqual(stress.fingerprint, liquidity_stress_config_fingerprint(stress))

    def test_config_changes_with_parameter(self) -> None:
        base = default_liquidity_stresses()[0]
        other = build_liquidity_stress_config(
            version=base.version,
            source=base.source,
            participation_rate=0.04,
            on_unfilled=base.on_unfilled,
            suspension=base.suspension,
        )
        self.assertNotEqual(base.fingerprint, other.fingerprint)

    def test_scenario_sha256_rederivable(self) -> None:
        scenario = _scenario()
        self.assertEqual(len(scenario.fingerprint), 64)
        int(scenario.fingerprint, 16)
        self.assertEqual(scenario.fingerprint, liquidity_stress_fingerprint(scenario))

    def test_scenario_stable(self) -> None:
        self.assertEqual(_scenario().fingerprint, _scenario().fingerprint)

    def test_scenario_changes_with_stress(self) -> None:
        self.assertNotEqual(
            _scenario().fingerprint,
            _scenario(stress_config=default_liquidity_stresses()[1]).fingerprint,
        )

    def test_scenario_changes_with_orders(self) -> None:
        def fewer_orders(fold) -> tuple[ExecutionDay, ...]:
            return _execution_days(fold)[:1]

        self.assertNotEqual(
            _scenario().fingerprint,
            _scenario(orders_for=fewer_orders).fingerprint,
        )

    def test_json_excludes_fingerprint_and_key_sorted(self) -> None:
        scenario = _scenario()
        serialized = liquidity_stress_json(scenario)
        self.assertNotIn('"fingerprint"', serialized)
        self.assertIn('"version":"liquidity-stress-tight"', serialized)
        self.assertIn('"refused_count":4', serialized)
        self.assertIn('"warning_count":4', serialized)
        payload = json.loads(serialized)
        self.assertEqual(
            serialized,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )

    def test_report_fingerprint_rederivable(self) -> None:
        report = compute_liquidity_stress_report(
            _oos_run(),
            orders_for=_execution_days,
            valuations_for=_valuation_days,
        )
        self.assertEqual(len(report.fingerprint), 64)
        self.assertEqual(report.fingerprint, liquidity_stress_report_fingerprint(report))
