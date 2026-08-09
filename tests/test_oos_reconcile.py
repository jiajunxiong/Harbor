"""Cross-market OOS reconciliation tests (MVP 3 / SP 3.40).

Verifies the FX, calendar, cost and corporate-action handling of the HK, US
and cross-market out-of-sample runs is reconciled separately per market
(分别核对 HK、US 和跨市场组合的 FX、日历、成本和企业行动处理), and that a
missing or non-positive FX rate continues to refuse computation (缺失 FX 继续
拒绝计算).
"""

import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from harbor.core.backtest_config import BenchmarkKind
from harbor.core.backtest_domain import Currency, Market
from harbor.core.benchmark import BenchmarkLevel, BenchmarkSeries
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.drawdown_events import DrawdownConfig, DrawdownSeries
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.holdout_registry import register_test_set
from harbor.core.market_registry import CorporateActionType
from harbor.core.oos_reconcile import (
    ComponentCheck,
    CrossMarketOosReconcile,
    MarketReconcile,
    MissingFxError,
    OosReconcileError,
    ReconcileComponent,
    oos_reconcile_fingerprint,
    oos_reconcile_json,
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
from harbor.core.rolling_oos import OosRunOutcome, run_rolling_oos
from harbor.core.rolling_train import run_rolling_training
from harbor.core.rolling_validate import (
    ValidationComponents,
    run_rolling_validation,
)
from harbor.core.rolling_window import build_walk_forward_folds
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
    ValidationStatus,
)

_FINGERPRINT = "f" * 64
_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_HK_HOLIDAY = frozenset({date(2023, 1, 2)})  # Monday, 2023 New Year's Day observed
_US_HOLIDAY = frozenset({date(2023, 1, 2)})  # Monday, 2023 New Year's Day observed


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
    """Return a three-parameter space valid for both HK and US."""
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


def _us_validation_run():
    """The SP 3.34 validation run on the US pipeline (same folds)."""
    return _validation_run(training_run=_training_run(market=Market.US))


def _us_oos_run():
    """The SP 3.35 OOS run on the US pipeline (same folds)."""
    return _oos_run(validation_run=_us_validation_run())


def _run_for_fingerprint(fp: str):
    """A full SP 3.35 run whose dataset fingerprint is ``fp``."""

    def fit_factory(train_start: date, train_end: date):
        return build_training_fit(
            fit_start=train_start,
            fit_end=train_end,
            dataset_fingerprint=fp,
            code_version="1.0.0",
            fitted_state=(("lookback", 252.0),),
        )

    return _oos_run(
        validation_run=_validation_run(
            training_run=_training_run(
                sequence=_sequence(dataset_fingerprint=fp),
                dataset_fingerprint=fp,
                fit_factory=fit_factory,
            )
        )
    )


def _calendar() -> MarketTradingCalendar:
    """A controlled calendar with a 2023-01-02 Monday holiday per market."""
    return MarketTradingCalendar(holidays={Market.HK: _HK_HOLIDAY, Market.US: _US_HOLIDAY})


def _fx_rate(quote: Currency, base: Currency, day: date) -> float | None:
    """Default FX: positive rate whenever the currencies differ."""
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


def _reconcile_kwargs() -> dict[str, object]:
    """The non-run keyword arguments of the reconciliation."""
    return {
        "base_currency": Currency.HKD,
        "calendar": _calendar(),
        "calendar_version": "cal-1",
        "fx_rate_for": _fx_rate,
        "trading_days_for": _trading_days,
        "cost_model_for": _cost_model,
        "corporate_actions_for": _corporate_actions,
    }


def _reconcile(**overrides: object) -> CrossMarketOosReconcile:
    """Compute the cross-market reconciliation with overridable arguments."""
    fields: dict[str, object] = {
        "oos_runs": {Market.HK: _oos_run(), Market.US: _us_oos_run()},
        **_reconcile_kwargs(),
    }
    fields.update(overrides)
    return reconcile_cross_market_oos(**fields)  # type: ignore[arg-type]


def _check(
    component: ReconcileComponent,
    market: Market = Market.HK,
    reconciled: bool = True,
    detail: str = "ok",
    reason: str | None = None,
) -> ComponentCheck:
    """A minimal component check for value-type tests."""
    return ComponentCheck(
        component=component,
        market=market,
        reconciled=reconciled,
        detail=detail,
        reason=reason,
    )


def _market_reconcile(**overrides: object) -> MarketReconcile:
    """A minimal market reconciliation for value-type tests."""
    fields: dict[str, object] = {
        "market": Market.HK,
        "base_currency": Currency.HKD,
        "quote_currency": Currency.HKD,
        "fx_required": False,
        "fold_count": 4,
        "executed_fold_count": 4,
        "oos_trading_days": 100,
        "checks": (
            _check(ReconcileComponent.FX),
            _check(ReconcileComponent.CALENDAR),
            _check(ReconcileComponent.COST),
            _check(ReconcileComponent.CORPORATE_ACTIONS),
        ),
    }
    fields.update(overrides)
    return MarketReconcile(**fields)  # type: ignore[arg-type]


class OosReconcileErrorTests(unittest.TestCase):
    """The dedicated error types and input-consistency guards."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(OosReconcileError, ValueError))

    def test_missing_fx_is_reconcile_error(self) -> None:
        self.assertTrue(issubclass(MissingFxError, OosReconcileError))

    def test_no_runs_rejected(self) -> None:
        with self.assertRaises(OosReconcileError):
            reconcile_cross_market_oos({}, **_reconcile_kwargs())  # type: ignore[arg-type]

    def test_different_dataset_fingerprints_rejected(self) -> None:
        us = _run_for_fingerprint("d" * 64)
        with self.assertRaises(OosReconcileError):
            reconcile_cross_market_oos(
                {Market.HK: _oos_run(), Market.US: us}, **_reconcile_kwargs()
            )

    def test_different_fold_counts_rejected(self) -> None:
        us = _oos_run(
            validation_run=_validation_run(
                training_run=_training_run(sequence=_sequence(rolling=_rolling(step_days=1000)))
            )
        )
        self.assertNotEqual(len(us.results), len(_oos_run().results))
        with self.assertRaises(OosReconcileError):
            reconcile_cross_market_oos(
                {Market.HK: _oos_run(), Market.US: us}, **_reconcile_kwargs()
            )


class ComponentCheckTests(unittest.TestCase):
    """The reconciled/failed invariants of a single component check."""

    def test_reconciled_check_is_valid(self) -> None:
        check = _check(ReconcileComponent.FX)
        self.assertTrue(check.reconciled)
        self.assertIsNone(check.reason)

    def test_failed_check_requires_reason(self) -> None:
        with self.assertRaises(OosReconcileError):
            _check(ReconcileComponent.FX, reconciled=False, reason=None)

    def test_reconciled_check_rejects_reason(self) -> None:
        with self.assertRaises(OosReconcileError):
            _check(ReconcileComponent.FX, reconciled=True, reason="boom")

    def test_empty_detail_rejected(self) -> None:
        with self.assertRaises(OosReconcileError):
            _check(ReconcileComponent.FX, detail="")

    def test_readable(self) -> None:
        self.assertIn("HK/fx reconciled", _check(ReconcileComponent.FX).readable())
        failed = _check(ReconcileComponent.FX, reconciled=False, reason="boom")
        self.assertIn("HK/fx FAILED: boom", failed.readable())


class MarketReconcileTests(unittest.TestCase):
    """The market reconciliation value type."""

    def test_valid_market_reconcile(self) -> None:
        result = _market_reconcile()
        self.assertTrue(result.reconciled)
        self.assertEqual(result.failures, ())

    def test_checks_must_cover_all_four_in_order(self) -> None:
        with self.assertRaises(OosReconcileError):
            _market_reconcile(
                checks=(
                    _check(ReconcileComponent.FX),
                    _check(ReconcileComponent.CALENDAR),
                    _check(ReconcileComponent.COST),
                )
            )
        with self.assertRaises(OosReconcileError):
            _market_reconcile(
                checks=(
                    _check(ReconcileComponent.CALENDAR),
                    _check(ReconcileComponent.FX),
                    _check(ReconcileComponent.COST),
                    _check(ReconcileComponent.CORPORATE_ACTIONS),
                )
            )

    def test_checks_must_match_the_market(self) -> None:
        with self.assertRaises(OosReconcileError):
            _market_reconcile(
                checks=(
                    _check(ReconcileComponent.FX),
                    _check(ReconcileComponent.CALENDAR, market=Market.US),
                    _check(ReconcileComponent.COST),
                    _check(ReconcileComponent.CORPORATE_ACTIONS),
                )
            )

    def test_invalid_fold_counts_rejected(self) -> None:
        with self.assertRaises(OosReconcileError):
            _market_reconcile(fold_count=-1)
        with self.assertRaises(OosReconcileError):
            _market_reconcile(executed_fold_count=5)
        with self.assertRaises(OosReconcileError):
            _market_reconcile(oos_trading_days=-1)

    def test_failures_surface(self) -> None:
        result = _market_reconcile(
            checks=(
                _check(ReconcileComponent.FX),
                _check(ReconcileComponent.CALENDAR, reconciled=False, reason="boom"),
                _check(ReconcileComponent.COST),
                _check(ReconcileComponent.CORPORATE_ACTIONS),
            )
        )
        self.assertFalse(result.reconciled)
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].component, ReconcileComponent.CALENDAR)

    def test_readable(self) -> None:
        result = _market_reconcile()
        self.assertIn("HK reconciled", result.readable())
        self.assertIn("no-fx", result.readable())


class FxReconciliationTests(unittest.TestCase):
    """FX handling: no-FX markets, required-FX markets, and refusal on gaps."""

    def test_hk_needs_no_fx_when_base_hkd(self) -> None:
        report = _reconcile(oos_runs={Market.HK: _oos_run()})
        result = report.market_results[0]
        fx = result.checks[0]
        self.assertFalse(result.fx_required)
        self.assertTrue(fx.reconciled)
        self.assertIn("no FX needed", fx.detail)

    def test_us_requires_fx_when_base_hkd(self) -> None:
        report = _reconcile(oos_runs={Market.US: _us_oos_run()})
        result = report.market_results[0]
        fx = result.checks[0]
        self.assertTrue(result.fx_required)
        self.assertEqual(result.quote_currency, Currency.USD)
        self.assertTrue(fx.reconciled)
        self.assertIn("USD->HKD", fx.detail)

    def test_cross_market_both_fx_reconciled(self) -> None:
        report = _reconcile()
        hk = report.market_results[0]
        us = report.market_results[1]
        self.assertFalse(hk.checks[0].reconciled is False)
        self.assertIn("no FX needed", hk.checks[0].detail)
        self.assertIn("USD->HKD", us.checks[0].detail)
        self.assertTrue(report.reconciled)

    def test_missing_fx_refuses(self) -> None:
        def missing(quote: Currency, base: Currency, day: date) -> float | None:
            if quote is base:
                return 1.0
            return None

        with self.assertRaises(MissingFxError):
            _reconcile(fx_rate_for=missing)

    def test_non_positive_fx_refuses(self) -> None:
        def zero(quote: Currency, base: Currency, day: date) -> float | None:
            if quote is base:
                return 1.0
            return 0.0

        with self.assertRaises(MissingFxError):
            _reconcile(fx_rate_for=zero)

    def test_single_us_market_still_needs_fx(self) -> None:
        report = _reconcile(oos_runs={Market.US: _us_oos_run()})
        self.assertTrue(report.market_results[0].fx_required)


class CalendarReconciliationTests(unittest.TestCase):
    """Calendar handling: OOS trading days land on the market calendar."""

    def test_all_trading_days_reconciled(self) -> None:
        report = _reconcile()
        for result in report:
            calendar = result.checks[1]
            self.assertTrue(calendar.reconciled)
            self.assertIn("trading day", calendar.detail)
            self.assertIn("cal-1", calendar.detail)

    def test_non_trading_day_fails(self) -> None:
        def bad_days(market: Market, fold) -> tuple[date, ...]:
            if fold.fold_index == 0:
                return (date(2023, 1, 2),)  # Monday holiday inside fold 0
            return _trading_days(market, fold)

        report = _reconcile(trading_days_for=bad_days)
        hk = report.market_results[0]
        self.assertFalse(hk.reconciled)
        calendar = hk.checks[1]
        self.assertFalse(calendar.reconciled)
        self.assertIsNotNone(calendar.reason)
        assert calendar.reason is not None
        self.assertIn("non-trading day", calendar.reason)

    def test_out_of_interval_day_fails(self) -> None:
        def bad_days(market: Market, fold) -> tuple[date, ...]:
            if fold.fold_index == 0:
                return (date(2022, 12, 31),)  # before fold 0's OOS interval
            return _trading_days(market, fold)

        report = _reconcile(trading_days_for=bad_days)
        calendar = report.market_results[0].checks[1]
        self.assertFalse(calendar.reconciled)
        self.assertIsNotNone(calendar.reason)
        assert calendar.reason is not None
        self.assertIn("outside its OOS interval", calendar.reason)

    def test_empty_trading_days_fails(self) -> None:
        def empty(market: Market, fold) -> tuple[date, ...]:
            if fold.fold_index == 0:
                return ()
            return _trading_days(market, fold)

        report = _reconcile(trading_days_for=empty)
        calendar = report.market_results[0].checks[1]
        self.assertFalse(calendar.reconciled)
        self.assertIsNotNone(calendar.reason)
        assert calendar.reason is not None
        self.assertIn("no OOS trading days", calendar.reason)

    def test_not_strictly_ascending_fails(self) -> None:
        def reversed_days(market: Market, fold) -> tuple[date, ...]:
            if fold.fold_index == 0:
                return (date(2023, 1, 4), date(2023, 1, 3))  # descending
            return _trading_days(market, fold)

        report = _reconcile(trading_days_for=reversed_days)
        calendar = report.market_results[0].checks[1]
        self.assertFalse(calendar.reconciled)
        self.assertIsNotNone(calendar.reason)
        assert calendar.reason is not None
        self.assertIn("not strictly ascending", calendar.reason)


class CostReconciliationTests(unittest.TestCase):
    """Cost handling: each market uses its own cost model (SP 2.37/2.38)."""

    def test_hk_uses_hk_model(self) -> None:
        report = _reconcile(oos_runs={Market.HK: _oos_run()})
        cost = report.market_results[0].checks[2]
        self.assertTrue(cost.reconciled)
        self.assertIn("hk cost model", cost.detail)

    def test_us_uses_us_model(self) -> None:
        report = _reconcile(oos_runs={Market.US: _us_oos_run()})
        cost = report.market_results[0].checks[2]
        self.assertTrue(cost.reconciled)
        self.assertIn("us cost model", cost.detail)

    def test_hk_using_us_model_fails(self) -> None:
        def wrong(market: Market, fold) -> str:
            return "us" if market is Market.HK else "hk"

        report = _reconcile(cost_model_for=wrong)
        cost = report.market_results[0].checks[2]
        self.assertFalse(cost.reconciled)
        self.assertIsNotNone(cost.reason)
        assert cost.reason is not None
        self.assertIn("expected 'hk'", cost.reason)

    def test_us_using_hk_model_fails(self) -> None:
        def wrong(market: Market, fold) -> str:
            return "us" if market is Market.HK else "hk"

        report = _reconcile(cost_model_for=wrong)
        cost = report.market_results[1].checks[2]
        self.assertFalse(cost.reconciled)
        self.assertIsNotNone(cost.reason)
        assert cost.reason is not None
        self.assertIn("expected 'us'", cost.reason)


class CorporateActionReconciliationTests(unittest.TestCase):
    """Corporate-action handling: HK and US rules are never mixed (SP 2.44)."""

    def test_hk_allowed_action_types(self) -> None:
        report = _reconcile(oos_runs={Market.HK: _oos_run()})
        actions = report.market_results[0].checks[3]
        self.assertTrue(actions.reconciled)
        self.assertIn("HK-allowed", actions.detail)

    def test_us_allowed_action_types(self) -> None:
        report = _reconcile(oos_runs={Market.US: _us_oos_run()})
        actions = report.market_results[0].checks[3]
        self.assertTrue(actions.reconciled)
        self.assertIn("US-allowed", actions.detail)

    def test_hk_processing_us_action_fails(self) -> None:
        def wrong(market: Market, fold) -> tuple[CorporateActionType, ...]:
            if market is Market.HK:
                return (CorporateActionType.SPLIT,)
            return _corporate_actions(market, fold)

        report = _reconcile(corporate_actions_for=wrong)
        actions = report.market_results[0].checks[3]
        self.assertFalse(actions.reconciled)
        self.assertIsNotNone(actions.reason)
        assert actions.reason is not None
        self.assertIn("split", actions.reason)
        self.assertIn("not allowed", actions.reason)

    def test_us_processing_hk_action_fails(self) -> None:
        def wrong(market: Market, fold) -> tuple[CorporateActionType, ...]:
            if market is Market.US:
                return (CorporateActionType.RIGHTS_ISSUE,)
            return _corporate_actions(market, fold)

        report = _reconcile(corporate_actions_for=wrong)
        actions = report.market_results[1].checks[3]
        self.assertFalse(actions.reconciled)
        self.assertIsNotNone(actions.reason)
        assert actions.reason is not None
        self.assertIn("rights_issue", actions.reason)
        self.assertIn("not allowed", actions.reason)


class CrossMarketReportTests(unittest.TestCase):
    """The aggregate cross-market reconciliation report."""

    def test_both_markets_reconciled(self) -> None:
        report = _reconcile()
        self.assertTrue(report.reconciled)
        self.assertEqual(len(report), 2)
        self.assertEqual(tuple(report.markets), (Market.HK, Market.US))

    def test_market_results_ordered(self) -> None:
        report = _reconcile()
        self.assertEqual(report.market_results[0].market, Market.HK)
        self.assertEqual(report.market_results[1].market, Market.US)

    def test_aggregate_counts(self) -> None:
        report = _reconcile()
        self.assertEqual(report.fold_count, 8)
        self.assertEqual(report.executed_fold_count, 8)
        self.assertGreater(report.oos_trading_days, 0)

    def test_failures_surface_across_markets(self) -> None:
        def wrong(market: Market, fold) -> str:
            return "us" if market is Market.HK else "hk"

        report = _reconcile(cost_model_for=wrong)
        self.assertFalse(report.reconciled)
        self.assertEqual(len(report.failures), 2)
        self.assertEqual(report.failures[0].component, ReconcileComponent.COST)

    def test_readable(self) -> None:
        report = _reconcile()
        text = report.readable()
        self.assertIn("HK+US", text)
        self.assertIn("base HKD", text)
        self.assertIn("reconciled", text)


class FingerprintTests(unittest.TestCase):
    """Stable, re-derivable, sensitive reconciliation fingerprints."""

    def test_sha256_hex(self) -> None:
        report = _reconcile()
        self.assertEqual(len(report.fingerprint), 64)
        int(report.fingerprint, 16)

    def test_rederivable(self) -> None:
        report = _reconcile()
        self.assertEqual(report.fingerprint, oos_reconcile_fingerprint(report))

    def test_stable(self) -> None:
        self.assertEqual(_reconcile().fingerprint, _reconcile().fingerprint)

    def test_changes_with_base_currency(self) -> None:
        self.assertNotEqual(
            _reconcile().fingerprint,
            _reconcile(base_currency=Currency.USD).fingerprint,
        )

    def test_changes_with_trading_days(self) -> None:
        def short(market: Market, fold) -> tuple[date, ...]:
            days = _trading_days(market, fold)
            return days[1:] if days else days

        self.assertNotEqual(
            _reconcile().fingerprint,
            _reconcile(trading_days_for=short).fingerprint,
        )

    def test_changes_with_corporate_actions(self) -> None:
        def extra(market: Market, fold) -> tuple[CorporateActionType, ...]:
            events = list(_corporate_actions(market, fold))
            events.append(CorporateActionType.DIVIDEND)
            return tuple(events)

        self.assertNotEqual(
            _reconcile().fingerprint,
            _reconcile(corporate_actions_for=extra).fingerprint,
        )

    def test_changes_with_cost_handling(self) -> None:
        def wrong(market: Market, fold) -> str:
            return "us" if market is Market.HK else "hk"

        self.assertNotEqual(
            _reconcile().fingerprint,
            _reconcile(cost_model_for=wrong).fingerprint,
        )

    def test_json_excludes_fingerprint_and_key_sorted(self) -> None:
        report = _reconcile()
        serialized = oos_reconcile_json(report)
        self.assertNotIn('"fingerprint"', serialized)
        payload = json.loads(serialized)
        self.assertEqual(
            serialized,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )
        self.assertIn('"base_currency":"HKD"', serialized)
        self.assertIn('"markets":["HK","US"]', serialized)
