"""FX stress tests (MVP 3 / SP 3.53).

Verifies that pre-registered FX delay, shock and missing scenarios are applied
to the cross-market OOS foreign flows and their base-currency impact quantified,
and that a missing FX rate is always refused rather than interpolated as 1:1
(对跨市场组合使用预注册 FX 延迟、冲击和缺失情景；缺失 FX 始终拒绝而非插补
1:1).
"""

import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from harbor.core.backtest_config import BenchmarkKind
from harbor.core.backtest_domain import Currency, Fill, Market, NetValue, OrderSide
from harbor.core.benchmark import BenchmarkLevel, BenchmarkSeries
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.drawdown_events import DrawdownConfig, DrawdownSeries
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.fx_stress import (
    FoldFxStress,
    FxRefusedFill,
    FxStressError,
    FxStressScenario,
    FxStressScenarioResult,
    build_fx_stress_config,
    compute_fx_stress_report,
    default_fx_stresses,
    fx_stress_config_fingerprint,
    fx_stress_fingerprint,
    fx_stress_json,
    fx_stress_report_fingerprint,
    quantify_fx_stress,
)
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


def _us_fill(fold, *, quantity: float = 10.0, price: float = 100.0) -> Fill:
    """A deterministic US (foreign) fill inside the fold."""
    return Fill(
        order_ref=f"fx-{fold.fold_index}",
        symbol="AAPL",
        market=Market.US,
        side=OrderSide.BUY,
        quantity=quantity,
        price=price,
        currency=Currency.USD,
        trade_date=fold.test_start + timedelta(days=10),
        fee=1.0,
    )


def _fills_for(fold) -> tuple[Fill, ...]:
    """One foreign (USD) fill per fold."""
    return (_us_fill(fold),)


def _hk_fills_for(fold) -> tuple[Fill, ...]:
    """A base-currency (HKD) fill, which needs no FX conversion."""
    return (
        Fill(
            order_ref=f"hk-{fold.fold_index}",
            symbol="0001.HK",
            market=Market.HK,
            side=OrderSide.BUY,
            quantity=1000.0,
            price=50.0,
            currency=Currency.HKD,
            trade_date=fold.test_start + timedelta(days=10),
            fee=1.0,
        ),
    )


def _fx_constant(from_currency, to_currency, as_of):
    """A constant USD->HKD rate of 7.8 (missing for other pairs)."""
    if from_currency is Currency.USD and to_currency is Currency.HKD:
        return 7.8
    return None


def _fx_drifted(from_currency, to_currency, as_of):
    """A drifting USD->HKD rate that rises 0.001 per OOS day."""
    if from_currency is Currency.USD and to_currency is Currency.HKD:
        return 7.8 + 0.001 * (as_of - _OOS_START).days
    return None


def _fx_none(from_currency, to_currency, as_of):
    """A feed with no FX rate at all."""
    return None


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


def _scenario(**overrides: object) -> FxStressScenarioResult:
    """Quantify the default +5% shock stress with overridable arguments."""
    fields: dict[str, object] = {
        "oos_run": _oos_run(),
        "stress_config": default_fx_stresses()[2],
        "base_currency": Currency.HKD,
        "fills_for": _fills_for,
        "net_values_for": _net_values_for,
        "fx_rate_for": _fx_constant,
    }
    fields.update(overrides)
    return quantify_fx_stress(**fields)  # type: ignore[arg-type]


def _scenario_kwargs() -> dict[str, object]:
    """The validated field values of one quantified scenario, for direct builds."""
    scenario = _scenario()
    return {
        "stress": scenario.stress,
        "folds": scenario.folds,
        "dataset_fingerprint": scenario.dataset_fingerprint,
        "code_version": scenario.code_version,
        "baseline_base_value": scenario.baseline_base_value,
        "stressed_base_value": scenario.stressed_base_value,
        "fx_impact": scenario.fx_impact,
        "baseline_final_net_value": scenario.baseline_final_net_value,
        "net_value_impact_pct": scenario.net_value_impact_pct,
        "refused_count": scenario.refused_count,
        "fingerprint": "x" * 64,
    }


def _scenario_direct(**overrides: object) -> FxStressScenarioResult:
    """A directly-constructed scenario (bypasses quantify) with overrides."""
    fields = _scenario_kwargs()
    fields.update(overrides)
    return FxStressScenarioResult(**fields)  # type: ignore[arg-type]


def _refused(**overrides: object) -> FxRefusedFill:
    """A minimal refused fill for value-level tests."""
    fields: dict[str, object] = {
        "market": Market.US,
        "symbol": "AAPL",
        "side": OrderSide.BUY,
        "quantity": 10.0,
        "price": 100.0,
        "currency": Currency.USD,
        "day": date(2023, 1, 11),
        "reason": "missing FX rate usd->hkd; refusing to assume 1:1 (SP 3.53).",
    }
    fields.update(overrides)
    return FxRefusedFill(**fields)  # type: ignore[arg-type]


class FxStressErrorTests(unittest.TestCase):
    """The dedicated error type."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(FxStressError, ValueError))

    def test_zero_shock_rejected(self) -> None:
        with self.assertRaises(FxStressError):
            build_fx_stress_config(version="v1", scenario=FxStressScenario.SHOCK, shock_bps=0.0)


class FxStressConfigTests(unittest.TestCase):
    """The pre-registered stressed FX scenarios."""

    def test_valid_delay(self) -> None:
        stress = default_fx_stresses()[0]
        self.assertEqual(stress.version, "fx-delay-1d")
        self.assertIs(stress.scenario, FxStressScenario.DELAY)
        self.assertEqual(stress.delay_days, 1)

    def test_valid_shock(self) -> None:
        stress = default_fx_stresses()[2]
        self.assertIs(stress.scenario, FxStressScenario.SHOCK)
        self.assertEqual(stress.shock_bps, 500.0)

    def test_valid_missing(self) -> None:
        stress = default_fx_stresses()[4]
        self.assertIs(stress.scenario, FxStressScenario.MISSING)
        self.assertEqual(stress.delay_days, 0)
        self.assertEqual(stress.shock_bps, 0.0)

    def test_delay_requires_days(self) -> None:
        with self.assertRaises(FxStressError):
            build_fx_stress_config(version="v1", scenario=FxStressScenario.DELAY, delay_days=0)

    def test_delay_must_not_carry_shock(self) -> None:
        with self.assertRaises(FxStressError):
            build_fx_stress_config(
                version="v1", scenario=FxStressScenario.DELAY, delay_days=1, shock_bps=100.0
            )

    def test_shock_must_not_carry_delay(self) -> None:
        with self.assertRaises(FxStressError):
            build_fx_stress_config(
                version="v1", scenario=FxStressScenario.SHOCK, shock_bps=100.0, delay_days=1
            )

    def test_missing_must_not_carry_parameters(self) -> None:
        with self.assertRaises(FxStressError):
            build_fx_stress_config(version="v1", scenario=FxStressScenario.MISSING, delay_days=1)

    def test_fingerprint_rederivable_stable(self) -> None:
        stress = default_fx_stresses()[0]
        self.assertEqual(stress.fingerprint, fx_stress_config_fingerprint(stress))
        self.assertEqual(stress.fingerprint, default_fx_stresses()[0].fingerprint)

    def test_default_range(self) -> None:
        stresses = default_fx_stresses()
        self.assertEqual(len(stresses), 5)
        self.assertEqual(
            [s.version for s in stresses],
            [
                "fx-delay-1d",
                "fx-delay-5d",
                "fx-shock-plus-5pct",
                "fx-shock-minus-5pct",
                "fx-missing",
            ],
        )
        self.assertEqual(
            [s.scenario for s in stresses],
            [
                FxStressScenario.DELAY,
                FxStressScenario.DELAY,
                FxStressScenario.SHOCK,
                FxStressScenario.SHOCK,
                FxStressScenario.MISSING,
            ],
        )

    def test_empty_version_rejected(self) -> None:
        with self.assertRaises(FxStressError):
            build_fx_stress_config(version="", scenario=FxStressScenario.DELAY, delay_days=1)

    def test_readable(self) -> None:
        self.assertIn("fx-delay-1d", default_fx_stresses()[0].readable())


class FxRefusedFillTests(unittest.TestCase):
    """The preserved foreign-fill refusal record."""

    def test_valid(self) -> None:
        refused = _refused()
        self.assertIn("refusing to assume 1:1", refused.reason)

    def test_empty_symbol_rejected(self) -> None:
        with self.assertRaises(FxStressError):
            _refused(symbol="")

    def test_empty_reason_rejected(self) -> None:
        with self.assertRaises(FxStressError):
            _refused(reason="")

    def test_readable(self) -> None:
        self.assertIn("AAPL", _refused().readable())


class FoldFxStressTests(unittest.TestCase):
    """One fold's stressed FX impact."""

    def test_valid_executed_fold(self) -> None:
        fold = FoldFxStress(
            fold_index=0,
            executed=True,
            baseline_base_value=7800.0,
            stressed_base_value=8190.0,
            fx_impact=390.0,
            refused_fills=(),
            failure_reason=None,
        )
        self.assertEqual(fold.fx_impact, 390.0)

    def test_valid_non_executed_fold(self) -> None:
        fold = FoldFxStress(
            fold_index=1,
            executed=False,
            baseline_base_value=0.0,
            stressed_base_value=0.0,
            fx_impact=0.0,
            refused_fills=(),
            failure_reason="denied",
        )
        self.assertFalse(fold.executed)

    def test_fx_impact_inconsistent_rejected(self) -> None:
        with self.assertRaises(FxStressError):
            FoldFxStress(
                fold_index=0,
                executed=True,
                baseline_base_value=7800.0,
                stressed_base_value=8190.0,
                fx_impact=999.0,
                refused_fills=(),
                failure_reason=None,
            )

    def test_non_executed_with_data_rejected(self) -> None:
        with self.assertRaises(FxStressError):
            FoldFxStress(
                fold_index=1,
                executed=False,
                baseline_base_value=100.0,
                stressed_base_value=0.0,
                fx_impact=-100.0,
                refused_fills=(),
                failure_reason="denied",
            )

    def test_readable(self) -> None:
        fold = FoldFxStress(
            fold_index=0,
            executed=True,
            baseline_base_value=7800.0,
            stressed_base_value=8190.0,
            fx_impact=390.0,
            refused_fills=(),
            failure_reason=None,
        )
        self.assertIn("+390.00", fold.readable())


class FxStressScenarioResultTests(unittest.TestCase):
    """The quantified scenario and its consistency invariants."""

    def test_valid_scenario(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.executed_count, 4)

    def test_empty_folds_rejected(self) -> None:
        with self.assertRaises(FxStressError):
            _scenario_direct(folds=())

    def test_no_executed_fold_rejected(self) -> None:
        scenario = _scenario()
        non_executed = tuple(
            FoldFxStress(
                fold_index=index,
                executed=False,
                baseline_base_value=0.0,
                stressed_base_value=0.0,
                fx_impact=0.0,
                refused_fills=(),
                failure_reason="denied",
            )
            for index in range(len(scenario.folds))
        )
        with self.assertRaises(FxStressError):
            _scenario_direct(
                folds=non_executed,
                refused_count=0,
                baseline_base_value=0.0,
                stressed_base_value=0.0,
                fx_impact=0.0,
                net_value_impact_pct=0.0,
            )

    def test_sum_inconsistent_rejected(self) -> None:
        with self.assertRaises(FxStressError):
            _scenario_direct(baseline_base_value=999.0)

    def test_refused_count_inconsistent_rejected(self) -> None:
        with self.assertRaises(FxStressError):
            _scenario_direct(refused_count=99)

    def test_impact_pct_inconsistent_rejected(self) -> None:
        with self.assertRaises(FxStressError):
            _scenario_direct(net_value_impact_pct=99.0)

    def test_len_iter_getitem(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario[0].fold_index, 0)
        self.assertEqual([fold.fold_index for fold in scenario], [0, 1, 2, 3])

    def test_readable(self) -> None:
        self.assertIn("fx-shock-plus-5pct", _scenario().readable())


class QuantifyFxStressTests(unittest.TestCase):
    """The pre-registered scenarios quantified on the OOS foreign flows."""

    def test_shock_plus_exact(self) -> None:
        scenario = _scenario()
        self.assertAlmostEqual(scenario.baseline_base_value, 31200.0, places=2)
        self.assertAlmostEqual(scenario.stressed_base_value, 32760.0, places=2)
        self.assertAlmostEqual(scenario.fx_impact, 1560.0, places=2)
        self.assertAlmostEqual(scenario.net_value_impact_pct, 0.156, places=6)
        self.assertEqual(scenario.refused_count, 0)

    def test_shock_minus_exact(self) -> None:
        scenario = _scenario(stress_config=default_fx_stresses()[3])
        self.assertAlmostEqual(scenario.fx_impact, -1560.0, places=2)
        self.assertAlmostEqual(scenario.net_value_impact_pct, -0.156, places=6)

    def test_per_fold_records(self) -> None:
        scenario = _scenario()
        for fold in scenario.folds:
            self.assertAlmostEqual(fold.baseline_base_value, 7800.0, places=2)
            self.assertAlmostEqual(fold.stressed_base_value, 8190.0, places=2)
            self.assertAlmostEqual(fold.fx_impact, 390.0, places=2)

    def test_delay_uses_stale_rate(self) -> None:
        delay_1d = _scenario(
            stress_config=default_fx_stresses()[0],
            fx_rate_for=_fx_drifted,
        )
        # the drifted rate rises 0.001/day, so a 1-day stale rate is -0.001/fill.
        self.assertAlmostEqual(delay_1d.fx_impact, -4.0, places=2)
        self.assertEqual(delay_1d.refused_count, 0)

    def test_longer_delay_larger_impact(self) -> None:
        delay_1d = _scenario(stress_config=default_fx_stresses()[0], fx_rate_for=_fx_drifted)
        delay_5d = _scenario(stress_config=default_fx_stresses()[1], fx_rate_for=_fx_drifted)
        self.assertAlmostEqual(delay_5d.fx_impact, -20.0, places=2)
        self.assertLess(delay_5d.fx_impact, delay_1d.fx_impact)

    def test_missing_refuses_every_fill(self) -> None:
        scenario = _scenario(stress_config=default_fx_stresses()[4])
        self.assertEqual(scenario.refused_count, 4)
        self.assertEqual(len(scenario.refused_fills), 4)
        self.assertAlmostEqual(scenario.baseline_base_value, 0.0, places=2)
        self.assertAlmostEqual(scenario.stressed_base_value, 0.0, places=2)
        self.assertAlmostEqual(scenario.fx_impact, 0.0, places=2)
        for refused in scenario.refused_fills:
            self.assertIn("refusing to assume 1:1", refused.reason)
            self.assertIn("fx-missing", refused.reason)

    def test_feed_gap_refuses(self) -> None:
        # even under a shock scenario, an actually-missing rate is refused.
        scenario = _scenario(fx_rate_for=_fx_none)
        self.assertEqual(scenario.refused_count, 4)
        self.assertEqual(len(scenario.refused_fills), 4)
        self.assertIn("refusing to assume 1:1", scenario.refused_fills[0].reason)

    def test_base_currency_fills_ignored(self) -> None:
        scenario = _scenario(fills_for=_hk_fills_for)
        self.assertAlmostEqual(scenario.baseline_base_value, 0.0, places=2)
        self.assertEqual(scenario.refused_count, 0)
        self.assertAlmostEqual(scenario.fx_impact, 0.0, places=2)

    def test_no_executed_fold_rejected(self) -> None:
        with self.assertRaises(FxStressError):
            _scenario(oos_run=_oos_run(current_stage=ValidationStatus.TUNING))


class ReportTests(unittest.TestCase):
    """The stressed FX impacts across the pre-registered range."""

    def test_report_has_five_scenarios(self) -> None:
        report = compute_fx_stress_report(
            _oos_run(),
            base_currency=Currency.HKD,
            fills_for=_fills_for,
            net_values_for=_net_values_for,
            fx_rate_for=_fx_constant,
        )
        self.assertEqual(len(report), 5)

    def test_scenario_lookup(self) -> None:
        report = compute_fx_stress_report(
            _oos_run(),
            base_currency=Currency.HKD,
            fills_for=_fills_for,
            net_values_for=_net_values_for,
            fx_rate_for=_fx_constant,
        )
        self.assertIsNotNone(report.scenario("fx-shock-plus-5pct"))
        self.assertIsNone(report.scenario("nope"))

    def test_missing_scenario_has_refusals(self) -> None:
        report = compute_fx_stress_report(
            _oos_run(),
            base_currency=Currency.HKD,
            fills_for=_fills_for,
            net_values_for=_net_values_for,
            fx_rate_for=_fx_constant,
        )
        missing = report.scenario("fx-missing")
        assert missing is not None
        self.assertEqual(missing.refused_count, 4)
        for scenario in report:
            if scenario.stress.version != "fx-missing":
                self.assertEqual(scenario.refused_count, 0)

    def test_report_context(self) -> None:
        report = compute_fx_stress_report(
            _oos_run(),
            base_currency=Currency.HKD,
            fills_for=_fills_for,
            net_values_for=_net_values_for,
            fx_rate_for=_fx_constant,
        )
        self.assertEqual(report.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(report.code_version, "1.0.0")

    def test_readable(self) -> None:
        report = compute_fx_stress_report(
            _oos_run(),
            base_currency=Currency.HKD,
            fills_for=_fills_for,
            net_values_for=_net_values_for,
            fx_rate_for=_fx_constant,
        )
        self.assertIn("5 FX stress scenario(s)", report.readable())


class FingerprintTests(unittest.TestCase):
    """Stable, re-derivable, sensitive FX-stress fingerprints."""

    def test_config_sha256_rederivable(self) -> None:
        stress = default_fx_stresses()[0]
        self.assertEqual(len(stress.fingerprint), 64)
        int(stress.fingerprint, 16)
        self.assertEqual(stress.fingerprint, fx_stress_config_fingerprint(stress))

    def test_config_changes_with_parameter(self) -> None:
        base = default_fx_stresses()[0]
        other = build_fx_stress_config(
            version="v1",
            source="pre-registered",
            scenario=FxStressScenario.DELAY,
            delay_days=3,
        )
        self.assertNotEqual(base.fingerprint, other.fingerprint)

    def test_scenario_sha256_rederivable(self) -> None:
        scenario = _scenario()
        self.assertEqual(len(scenario.fingerprint), 64)
        int(scenario.fingerprint, 16)
        self.assertEqual(scenario.fingerprint, fx_stress_fingerprint(scenario))

    def test_scenario_stable(self) -> None:
        self.assertEqual(_scenario().fingerprint, _scenario().fingerprint)

    def test_scenario_changes_with_stress(self) -> None:
        self.assertNotEqual(
            _scenario().fingerprint,
            _scenario(stress_config=default_fx_stresses()[3]).fingerprint,
        )

    def test_scenario_changes_with_fills(self) -> None:
        def two_fills(fold) -> tuple[Fill, ...]:
            return (
                _us_fill(fold),
                _us_fill(fold, quantity=5.0, price=80.0),
            )

        self.assertNotEqual(
            _scenario().fingerprint,
            _scenario(fills_for=two_fills).fingerprint,
        )

    def test_json_excludes_fingerprint_and_key_sorted(self) -> None:
        scenario = _scenario()
        serialized = fx_stress_json(scenario)
        self.assertNotIn('"fingerprint"', serialized)
        self.assertIn('"version":"fx-shock-plus-5pct"', serialized)
        payload = json.loads(serialized)
        self.assertAlmostEqual(payload["fx_impact"], 1560.0, places=4)
        self.assertEqual(
            serialized,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )

    def test_report_fingerprint_rederivable(self) -> None:
        report = compute_fx_stress_report(
            _oos_run(),
            base_currency=Currency.HKD,
            fills_for=_fills_for,
            net_values_for=_net_values_for,
            fx_rate_for=_fx_constant,
        )
        self.assertEqual(len(report.fingerprint), 64)
        self.assertEqual(report.fingerprint, fx_stress_report_fingerprint(report))
