"""Calendar and rebalance stress tests (MVP 3 / SP 3.54).

Verifies that rebalances meeting market closures, long holidays, delays and
different legal deferral rules are quantified, and that the authoritative
calendar version is recorded (检验调仓遇休市、长假、延迟与不同合法顺延规则的影
响，并记录权威日历版本).
"""

import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from harbor.core.backtest_config import BenchmarkKind
from harbor.core.backtest_domain import Market
from harbor.core.backtest_interfaces import TradingCalendar
from harbor.core.benchmark import BenchmarkLevel, BenchmarkSeries
from harbor.core.calendar_stress import (
    CalendarStressError,
    CalendarStressScenarioResult,
    RebalanceDayImpact,
    build_calendar_stress_config,
    calendar_stress_config_fingerprint,
    calendar_stress_fingerprint,
    calendar_stress_json,
    calendar_stress_report_fingerprint,
    compute_calendar_stress_report,
    default_calendar_stresses,
    quantify_calendar_stress,
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
from harbor.core.rebalance_schedule import DeferralRule
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
_OOS_START = date(2023, 1, 1)
_OOS_END = date(2026, 12, 30)
_ANCHORS = (date(2026, 1, 2), date(2026, 1, 3), date(2026, 4, 6), date(2026, 7, 1))


def _calendar_factory(market: Market):
    """A factory building a weekday calendar with the given market holidays."""

    def factory(holidays: frozenset[date]) -> TradingCalendar:
        return MarketTradingCalendar(holidays={market: holidays})

    return factory


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


def _scenario(**overrides: object) -> CalendarStressScenarioResult:
    """Quantify the default closure stress with overridable arguments."""
    fields: dict[str, object] = {
        "oos_run": _oos_run(),
        "stress_config": default_calendar_stresses()[0],
        "market": Market.HK,
        "anchors": _ANCHORS,
        "calendar_factory": _calendar_factory(Market.HK),
    }
    fields.update(overrides)
    return quantify_calendar_stress(**fields)  # type: ignore[arg-type]


def _scenario_kwargs() -> dict[str, object]:
    """The validated field values of one quantified scenario, for direct builds."""
    scenario = _scenario()
    return {
        "stress": scenario.stress,
        "market": scenario.market,
        "calendar_version": scenario.calendar_version,
        "dataset_fingerprint": scenario.dataset_fingerprint,
        "code_version": scenario.code_version,
        "impacts": scenario.impacts,
        "deferred_count": scenario.deferred_count,
        "stress_closed_count": scenario.stress_closed_count,
        "total_shift_days": scenario.total_shift_days,
        "max_shift_days": scenario.max_shift_days,
        "fingerprint": "x" * 64,
    }


def _scenario_direct(**overrides: object) -> CalendarStressScenarioResult:
    """A directly-constructed scenario (bypasses quantify) with overrides."""
    fields = _scenario_kwargs()
    fields.update(overrides)
    return CalendarStressScenarioResult(**fields)  # type: ignore[arg-type]


def _impact(**overrides: object) -> RebalanceDayImpact:
    """A minimal impact for value-level tests."""
    fields: dict[str, object] = {
        "anchor": date(2026, 1, 2),
        "trading_day": False,
        "stress_closed": True,
        "scheduled": date(2026, 1, 5),
        "shift_days": 3,
        "reason": "deferred forward 3 day(s) to a trading day",
    }
    fields.update(overrides)
    return RebalanceDayImpact(**fields)  # type: ignore[arg-type]


class CalendarStressErrorTests(unittest.TestCase):
    """The dedicated error type."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(CalendarStressError, ValueError))

    def test_unsorted_holidays_rejected(self) -> None:
        with self.assertRaises(CalendarStressError):
            build_calendar_stress_config(
                version="v1",
                holidays=(date(2026, 1, 5), date(2026, 1, 2)),
                deferral_rule=DeferralRule.FORWARD,
                calendar_version="cal-2026a",
            )


class CalendarStressConfigTests(unittest.TestCase):
    """The pre-registered stressed calendar scenarios."""

    def test_valid_config(self) -> None:
        stress = default_calendar_stresses()[0]
        self.assertEqual(stress.version, "calendar-stress-closure")
        self.assertEqual(stress.holidays, (date(2026, 1, 2),))
        self.assertIs(stress.deferral_rule, DeferralRule.FORWARD)
        self.assertEqual(stress.calendar_version, "cal-2026a")

    def test_default_range(self) -> None:
        stresses = default_calendar_stresses()
        self.assertEqual(len(stresses), 3)
        self.assertEqual(
            [s.version for s in stresses],
            [
                "calendar-stress-closure",
                "calendar-stress-long-holiday",
                "calendar-stress-backward",
            ],
        )
        self.assertEqual(
            [s.deferral_rule for s in stresses],
            [DeferralRule.FORWARD, DeferralRule.FORWARD, DeferralRule.BACKWARD],
        )
        # the long holiday injects a full trading week.
        self.assertEqual(len(stresses[1].holidays), 5)

    def test_fingerprint_rederivable_stable(self) -> None:
        stress = default_calendar_stresses()[0]
        self.assertEqual(stress.fingerprint, calendar_stress_config_fingerprint(stress))
        self.assertEqual(stress.fingerprint, default_calendar_stresses()[0].fingerprint)

    def test_empty_version_rejected(self) -> None:
        with self.assertRaises(CalendarStressError):
            build_calendar_stress_config(
                version="",
                holidays=(),
                deferral_rule=DeferralRule.FORWARD,
                calendar_version="cal-2026a",
            )

    def test_empty_calendar_version_rejected(self) -> None:
        with self.assertRaises(CalendarStressError):
            build_calendar_stress_config(
                version="v1",
                holidays=(),
                deferral_rule=DeferralRule.FORWARD,
                calendar_version="",
            )

    def test_duplicate_holidays_rejected(self) -> None:
        with self.assertRaises(CalendarStressError):
            build_calendar_stress_config(
                version="v1",
                holidays=(date(2026, 1, 2), date(2026, 1, 2)),
                deferral_rule=DeferralRule.FORWARD,
                calendar_version="cal-2026a",
            )

    def test_readable(self) -> None:
        self.assertIn("calendar-stress-closure", default_calendar_stresses()[0].readable())


class RebalanceDayImpactTests(unittest.TestCase):
    """One rebalance anchor's scheduled day under stress."""

    def test_valid_deferred(self) -> None:
        impact = _impact()
        self.assertFalse(impact.trading_day)
        self.assertTrue(impact.stress_closed)
        self.assertEqual(impact.shift_days, 3)

    def test_valid_on_trading_day(self) -> None:
        impact = _impact(
            anchor=date(2026, 7, 1),
            trading_day=True,
            stress_closed=False,
            scheduled=date(2026, 7, 1),
            shift_days=0,
            reason="rebalance on a trading day",
        )
        self.assertTrue(impact.trading_day)

    def test_shift_inconsistent_rejected(self) -> None:
        with self.assertRaises(CalendarStressError):
            _impact(shift_days=99)

    def test_trading_day_mismatch_rejected(self) -> None:
        with self.assertRaises(CalendarStressError):
            _impact(trading_day=True)

    def test_stress_closed_on_trading_day_rejected(self) -> None:
        with self.assertRaises(CalendarStressError):
            _impact(
                trading_day=True,
                stress_closed=True,
                scheduled=date(2026, 1, 2),
                shift_days=0,
                reason="rebalance on a trading day",
            )

    def test_readable(self) -> None:
        self.assertIn("2026-01-02 -> 2026-01-05", _impact().readable())


class CalendarStressScenarioResultTests(unittest.TestCase):
    """The quantified scenario and its consistency invariants."""

    def test_valid_scenario(self) -> None:
        scenario = _scenario()
        self.assertEqual(len(scenario.impacts), 4)

    def test_empty_impacts_rejected(self) -> None:
        with self.assertRaises(CalendarStressError):
            _scenario_direct(
                impacts=(),
                deferred_count=0,
                stress_closed_count=0,
                total_shift_days=0,
                max_shift_days=0,
            )

    def test_deferred_count_inconsistent_rejected(self) -> None:
        with self.assertRaises(CalendarStressError):
            _scenario_direct(deferred_count=99)

    def test_total_shift_inconsistent_rejected(self) -> None:
        with self.assertRaises(CalendarStressError):
            _scenario_direct(total_shift_days=99)

    def test_max_shift_inconsistent_rejected(self) -> None:
        with self.assertRaises(CalendarStressError):
            _scenario_direct(max_shift_days=99)

    def test_impact_for_lookup(self) -> None:
        scenario = _scenario()
        self.assertIsNotNone(scenario.impact_for(date(2026, 1, 2)))
        self.assertIsNone(scenario.impact_for(date(2027, 1, 1)))

    def test_len_iter_getitem(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario[0].anchor, date(2026, 1, 2))
        self.assertEqual(len(list(scenario)), 4)

    def test_readable(self) -> None:
        self.assertIn("calendar-stress-closure", _scenario().readable())


class QuantifyCalendarStressTests(unittest.TestCase):
    """The closure, long-holiday and deferral-rule impacts."""

    def test_closure_exact(self) -> None:
        scenario = _scenario()
        closure = scenario.impact_for(date(2026, 1, 2))
        assert closure is not None
        self.assertTrue(closure.stress_closed)
        self.assertEqual(closure.scheduled, date(2026, 1, 5))
        self.assertEqual(closure.shift_days, 3)
        weekend = scenario.impact_for(date(2026, 1, 3))
        assert weekend is not None
        self.assertFalse(weekend.stress_closed)  # closed by the weekend, not the stress
        self.assertEqual(weekend.scheduled, date(2026, 1, 5))
        self.assertEqual(weekend.shift_days, 2)

    def test_closure_aggregates(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.deferred_count, 2)
        self.assertEqual(scenario.stress_closed_count, 1)
        self.assertEqual(scenario.total_shift_days, 5)
        self.assertEqual(scenario.max_shift_days, 3)

    def test_long_holiday_exact(self) -> None:
        scenario = _scenario(stress_config=default_calendar_stresses()[1])
        long_holiday = scenario.impact_for(date(2026, 4, 6))
        assert long_holiday is not None
        self.assertTrue(long_holiday.stress_closed)
        self.assertEqual(long_holiday.scheduled, date(2026, 4, 13))
        self.assertEqual(long_holiday.shift_days, 7)
        self.assertEqual(scenario.total_shift_days, 9)
        self.assertEqual(scenario.max_shift_days, 7)

    def test_long_holiday_larger_than_closure(self) -> None:
        closure = _scenario()
        long_holiday = _scenario(stress_config=default_calendar_stresses()[1])
        self.assertGreater(long_holiday.max_shift_days, closure.max_shift_days)

    def test_backward_rule(self) -> None:
        scenario = _scenario(stress_config=default_calendar_stresses()[2])
        backward = scenario.impact_for(date(2026, 1, 2))
        assert backward is not None
        self.assertEqual(backward.scheduled, date(2026, 1, 1))
        self.assertEqual(backward.shift_days, -1)
        weekend = scenario.impact_for(date(2026, 1, 3))
        assert weekend is not None
        self.assertEqual(weekend.scheduled, date(2026, 1, 1))
        self.assertEqual(weekend.shift_days, -2)
        self.assertEqual(scenario.total_shift_days, 3)
        self.assertEqual(scenario.max_shift_days, 2)

    def test_different_rules_different_schedule(self) -> None:
        forward = _scenario()
        backward = _scenario(stress_config=default_calendar_stresses()[2])
        self.assertEqual(
            forward.impact_for(date(2026, 1, 2)).scheduled,
            date(2026, 1, 5),
        )
        self.assertEqual(
            backward.impact_for(date(2026, 1, 2)).scheduled,
            date(2026, 1, 1),
        )

    def test_on_trading_day_anchor_unaffected(self) -> None:
        scenario = _scenario()
        july = scenario.impact_for(date(2026, 7, 1))
        assert july is not None
        self.assertTrue(july.trading_day)
        self.assertEqual(july.shift_days, 0)

    def test_calendar_version_recorded(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.calendar_version, "cal-2026a")

    def test_report_context(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(scenario.code_version, "1.0.0")
        self.assertIs(scenario.market, Market.HK)

    def test_empty_anchors_rejected(self) -> None:
        with self.assertRaises(CalendarStressError):
            _scenario(anchors=())


class ReportTests(unittest.TestCase):
    """The stressed outcomes across the pre-registered range."""

    def test_report_has_three_scenarios(self) -> None:
        report = compute_calendar_stress_report(
            _oos_run(),
            market=Market.HK,
            anchors=_ANCHORS,
            calendar_factory=_calendar_factory(Market.HK),
        )
        self.assertEqual(len(report), 3)

    def test_scenario_lookup(self) -> None:
        report = compute_calendar_stress_report(
            _oos_run(),
            market=Market.HK,
            anchors=_ANCHORS,
            calendar_factory=_calendar_factory(Market.HK),
        )
        self.assertIsNotNone(report.scenario("calendar-stress-closure"))
        self.assertIsNone(report.scenario("nope"))

    def test_calendar_version_shared(self) -> None:
        report = compute_calendar_stress_report(
            _oos_run(),
            market=Market.HK,
            anchors=_ANCHORS,
            calendar_factory=_calendar_factory(Market.HK),
        )
        self.assertEqual(report.calendar_version, "cal-2026a")
        self.assertTrue(all(s.calendar_version == "cal-2026a" for s in report))

    def test_long_holiday_largest_shift(self) -> None:
        report = compute_calendar_stress_report(
            _oos_run(),
            market=Market.HK,
            anchors=_ANCHORS,
            calendar_factory=_calendar_factory(Market.HK),
        )
        closure = report.scenario("calendar-stress-closure")
        long_holiday = report.scenario("calendar-stress-long-holiday")
        assert closure is not None
        assert long_holiday is not None
        self.assertGreater(long_holiday.max_shift_days, closure.max_shift_days)

    def test_readable(self) -> None:
        report = compute_calendar_stress_report(
            _oos_run(),
            market=Market.HK,
            anchors=_ANCHORS,
            calendar_factory=_calendar_factory(Market.HK),
        )
        self.assertIn("3 calendar stress scenario(s)", report.readable())


class FingerprintTests(unittest.TestCase):
    """Stable, re-derivable, sensitive calendar-stress fingerprints."""

    def test_config_sha256_rederivable(self) -> None:
        stress = default_calendar_stresses()[0]
        self.assertEqual(len(stress.fingerprint), 64)
        int(stress.fingerprint, 16)
        self.assertEqual(stress.fingerprint, calendar_stress_config_fingerprint(stress))

    def test_config_changes_with_holidays(self) -> None:
        base = default_calendar_stresses()[0]
        other = build_calendar_stress_config(
            version="v1",
            source="pre-registered",
            holidays=(date(2026, 2, 2),),
            deferral_rule=base.deferral_rule,
            calendar_version=base.calendar_version,
        )
        self.assertNotEqual(base.fingerprint, other.fingerprint)

    def test_scenario_sha256_rederivable(self) -> None:
        scenario = _scenario()
        self.assertEqual(len(scenario.fingerprint), 64)
        int(scenario.fingerprint, 16)
        self.assertEqual(scenario.fingerprint, calendar_stress_fingerprint(scenario))

    def test_scenario_stable(self) -> None:
        self.assertEqual(_scenario().fingerprint, _scenario().fingerprint)

    def test_scenario_changes_with_stress(self) -> None:
        self.assertNotEqual(
            _scenario().fingerprint,
            _scenario(stress_config=default_calendar_stresses()[1]).fingerprint,
        )

    def test_scenario_changes_with_anchors(self) -> None:
        self.assertNotEqual(
            _scenario().fingerprint,
            _scenario(anchors=(date(2026, 7, 1),)).fingerprint,
        )

    def test_json_excludes_fingerprint_and_key_sorted(self) -> None:
        scenario = _scenario()
        serialized = calendar_stress_json(scenario)
        self.assertNotIn('"fingerprint"', serialized)
        self.assertIn('"version":"calendar-stress-closure"', serialized)
        self.assertIn('"calendar_version":"cal-2026a"', serialized)
        self.assertIn('"deferred_count":2', serialized)
        payload = json.loads(serialized)
        self.assertEqual(
            serialized,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )

    def test_report_fingerprint_rederivable(self) -> None:
        report = compute_calendar_stress_report(
            _oos_run(),
            market=Market.HK,
            anchors=_ANCHORS,
            calendar_factory=_calendar_factory(Market.HK),
        )
        self.assertEqual(len(report.fingerprint), 64)
        self.assertEqual(report.fingerprint, calendar_stress_report_fingerprint(report))
