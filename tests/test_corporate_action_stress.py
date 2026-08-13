"""Corporate-action data stress tests (MVP 3 / SP 3.55).

Verifies that conservative scenarios for missing terms (条款缺失), delayed
registration (延迟登记), pending-review events (待复核事件) and price-adjustment
deviations (价格调整偏差) are quantified, and that a key unknown makes the
conclusion NOT_QUALIFIED (关键未知项使结论不合格).
"""

import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from harbor.core.adjustments import ActionTerms
from harbor.core.backtest_config import BenchmarkKind
from harbor.core.backtest_domain import Market
from harbor.core.benchmark import BenchmarkLevel, BenchmarkSeries
from harbor.core.corporate_action_stress import (
    CorporateActionFinding,
    CorporateActionStressError,
    CorporateActionStressInput,
    CorporateActionStressKind,
    CorporateActionStressScenarioResult,
    build_corporate_action_stress_config,
    compute_corporate_action_stress_report,
    corporate_action_stress_config_fingerprint,
    corporate_action_stress_fingerprint,
    corporate_action_stress_json,
    corporate_action_stress_report_fingerprint,
    default_corporate_action_stresses,
    quantify_corporate_action_stress,
)
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.drawdown_events import DrawdownConfig, DrawdownSeries
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.holdout_registry import register_test_set
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
from harbor.core.test_access_guard import AccessGuard
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


def _event(**overrides: object) -> CorporateActionStressInput:
    """A minimal US SPLIT event for stress tests."""
    fields: dict[str, object] = {
        "symbol": "AAPL",
        "action_id": "ca-1",
        "action_type": CorporateActionType.SPLIT,
        "snapshot_date": date(2026, 1, 10),
        "terms": ActionTerms(ratio=2.0),
        "record_date": None,
        "ex_date": date(2026, 1, 15),
        "registered_at": None,
        "pending_review": False,
        "adjustment_factor": None,
        "expected_adjustment": None,
    }
    fields.update(overrides)
    return CorporateActionStressInput(**fields)  # type: ignore[arg-type]


def _complete_event(**overrides: object) -> CorporateActionStressInput:
    """A fully-complete US SPLIT event that triggers no scenario."""
    fields: dict[str, object] = {
        "symbol": "AAPL",
        "action_id": "ca-ok",
        "action_type": CorporateActionType.SPLIT,
        "snapshot_date": date(2026, 1, 10),
        "terms": ActionTerms(ratio=2.0),
        "record_date": None,
        "ex_date": date(2026, 1, 15),
        "registered_at": date(2026, 1, 5),
        "pending_review": False,
        "adjustment_factor": 0.5,
        "expected_adjustment": 0.5,
    }
    fields.update(overrides)
    return CorporateActionStressInput(**fields)  # type: ignore[arg-type]


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


def _scenario(**overrides: object) -> CorporateActionStressScenarioResult:
    """Quantify the default missing-terms stress with overridable arguments."""
    fields: dict[str, object] = {
        "oos_run": _oos_run(),
        "stress_config": default_corporate_action_stresses()[0],
        "market": Market.US,
        "events": (_event(terms=ActionTerms()),),
    }
    fields.update(overrides)
    return quantify_corporate_action_stress(**fields)  # type: ignore[arg-type]


def _scenario_kwargs() -> dict[str, object]:
    """The validated field values of one quantified scenario, for direct builds."""
    scenario = _scenario()
    return {
        "stress": scenario.stress,
        "market": scenario.market,
        "dataset_fingerprint": scenario.dataset_fingerprint,
        "code_version": scenario.code_version,
        "findings": scenario.findings,
        "conclusion_severity": scenario.conclusion_severity,
        "fingerprint": "x" * 64,
    }


def _scenario_direct(**overrides: object) -> CorporateActionStressScenarioResult:
    """A directly-constructed scenario (bypasses quantify) with overrides."""
    fields = _scenario_kwargs()
    fields.update(overrides)
    return CorporateActionStressScenarioResult(**fields)  # type: ignore[arg-type]


class CorporateActionStressErrorTests(unittest.TestCase):
    """The dedicated error type."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(CorporateActionStressError, ValueError))

    def test_empty_version_rejected(self) -> None:
        with self.assertRaises(CorporateActionStressError):
            build_corporate_action_stress_config(
                version="",
                kind=CorporateActionStressKind.MISSING_TERMS,
                severity=CoverageSeverity.NOT_QUALIFIED,
            )


class CorporateActionStressKindTests(unittest.TestCase):
    """The pre-registered scenario kinds."""

    def test_four_kinds(self) -> None:
        self.assertEqual(
            tuple(CorporateActionStressKind),
            (
                CorporateActionStressKind.MISSING_TERMS,
                CorporateActionStressKind.DELAYED_REGISTRATION,
                CorporateActionStressKind.PENDING_REVIEW,
                CorporateActionStressKind.PRICE_ADJUSTMENT_DEVIATION,
            ),
        )
        self.assertEqual(CorporateActionStressKind.MISSING_TERMS.value, "missing_terms")


class CorporateActionStressInputTests(unittest.TestCase):
    """The injected event context."""

    def test_valid(self) -> None:
        event = _event()
        self.assertEqual(event.symbol, "AAPL")

    def test_empty_symbol_rejected(self) -> None:
        with self.assertRaises(CorporateActionStressError):
            _event(symbol="")

    def test_empty_action_id_rejected(self) -> None:
        with self.assertRaises(CorporateActionStressError):
            _event(action_id="")


class CorporateActionStressConfigTests(unittest.TestCase):
    """The pre-registered conservative scenarios."""

    def test_valid_config(self) -> None:
        stress = default_corporate_action_stresses()[0]
        self.assertEqual(stress.version, "ca-missing-terms")
        self.assertIs(stress.kind, CorporateActionStressKind.MISSING_TERMS)
        self.assertIs(stress.severity, CoverageSeverity.NOT_QUALIFIED)

    def test_default_range(self) -> None:
        stresses = default_corporate_action_stresses()
        self.assertEqual(len(stresses), 4)
        self.assertEqual(
            [s.version for s in stresses],
            [
                "ca-missing-terms",
                "ca-delayed-registration",
                "ca-pending-review",
                "ca-price-adjustment-deviation",
            ],
        )
        # the three unknown scenarios make the conclusion NOT_QUALIFIED.
        for stress in stresses[:3]:
            self.assertIs(stress.severity, CoverageSeverity.NOT_QUALIFIED)
        self.assertIs(
            stresses[3].severity,
            CoverageSeverity.WARNING,
        )

    def test_fingerprint_rederivable_stable(self) -> None:
        stress = default_corporate_action_stresses()[0]
        self.assertEqual(stress.fingerprint, corporate_action_stress_config_fingerprint(stress))
        self.assertEqual(stress.fingerprint, default_corporate_action_stresses()[0].fingerprint)

    def test_empty_source_rejected(self) -> None:
        with self.assertRaises(CorporateActionStressError):
            build_corporate_action_stress_config(
                version="v1",
                source="",
                kind=CorporateActionStressKind.MISSING_TERMS,
                severity=CoverageSeverity.NOT_QUALIFIED,
            )

    def test_readable(self) -> None:
        self.assertIn("ca-missing-terms", default_corporate_action_stresses()[0].readable())


class CorporateActionFindingTests(unittest.TestCase):
    """The preserved finding record."""

    def test_valid(self) -> None:
        finding = _scenario().findings[0]
        self.assertEqual(finding.symbol, "AAPL")
        self.assertEqual(finding.action_id, "ca-1")

    def test_empty_message_rejected(self) -> None:
        with self.assertRaises(CorporateActionStressError):
            CorporateActionFinding(
                kind=CorporateActionStressKind.MISSING_TERMS,
                market=Market.US,
                symbol="AAPL",
                action_id="ca-1",
                action_type=CorporateActionType.SPLIT,
                day=date(2026, 1, 15),
                severity=CoverageSeverity.NOT_QUALIFIED,
                message="",
            )

    def test_readable(self) -> None:
        finding = _scenario().findings[0]
        self.assertIn("AAPL", finding.readable())
        self.assertIn(
            "not_qualified" if finding.severity is CoverageSeverity.NOT_QUALIFIED else "",
            finding.readable(),
        )


class CorporateActionStressScenarioResultTests(unittest.TestCase):
    """The quantified scenario and its consistency invariants."""

    def test_valid_with_findings(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.finding_count, 1)
        self.assertTrue(scenario.not_qualified)
        self.assertIs(scenario.conclusion_severity, CoverageSeverity.NOT_QUALIFIED)

    def test_clean_scenario(self) -> None:
        scenario = _scenario(events=(_complete_event(),))
        self.assertEqual(scenario.finding_count, 0)
        self.assertFalse(scenario.not_qualified)
        self.assertIsNone(scenario.conclusion_severity)

    def test_conclusion_severity_mismatch_rejected(self) -> None:
        with self.assertRaises(CorporateActionStressError):
            _scenario_direct(conclusion_severity=None)

    def test_finding_kind_mismatch_rejected(self) -> None:
        scenario = _scenario()
        finding = replace(scenario.findings[0], kind=CorporateActionStressKind.PENDING_REVIEW)
        with self.assertRaises(CorporateActionStressError):
            _scenario_direct(findings=(finding,))

    def test_finding_severity_mismatch_rejected(self) -> None:
        scenario = _scenario()
        finding = replace(scenario.findings[0], severity=CoverageSeverity.WARNING)
        with self.assertRaises(CorporateActionStressError):
            _scenario_direct(findings=(finding,))

    def test_len_iter_getitem(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario[0].action_id, "ca-1")
        self.assertEqual(len(list(scenario)), 1)

    def test_readable(self) -> None:
        self.assertIn("ca-missing-terms", _scenario().readable())


class QuantifyCorporateActionStressTests(unittest.TestCase):
    """The four conservative scenarios quantified on the OOS events."""

    def test_missing_terms_finding(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.finding_count, 1)
        self.assertIn("missing required terms", scenario.findings[0].message)
        self.assertEqual(scenario.findings[0].day, date(2026, 1, 15))

    def test_missing_terms_complete_clean(self) -> None:
        scenario = _scenario(events=(_complete_event(),))
        self.assertEqual(scenario.finding_count, 0)

    def test_delayed_registration_finding(self) -> None:
        scenario = _scenario(
            stress_config=default_corporate_action_stresses()[1],
            events=(_event(registered_at=date(2026, 1, 12)),),
        )
        self.assertEqual(scenario.finding_count, 1)
        self.assertIn("after the snapshot", scenario.findings[0].message)
        self.assertTrue(scenario.not_qualified)

    def test_delayed_registration_on_time_clean(self) -> None:
        scenario = _scenario(
            stress_config=default_corporate_action_stresses()[1],
            events=(_event(registered_at=date(2026, 1, 8)),),
        )
        self.assertEqual(scenario.finding_count, 0)

    def test_pending_review_finding(self) -> None:
        scenario = _scenario(
            stress_config=default_corporate_action_stresses()[2],
            events=(_event(pending_review=True),),
        )
        self.assertEqual(scenario.finding_count, 1)
        self.assertIn("pending manual review", scenario.findings[0].message)
        self.assertTrue(scenario.not_qualified)

    def test_pending_review_clean(self) -> None:
        scenario = _scenario(
            stress_config=default_corporate_action_stresses()[2],
            events=(_event(pending_review=False),),
        )
        self.assertEqual(scenario.finding_count, 0)

    def test_price_adjustment_deviation_finding(self) -> None:
        scenario = _scenario(
            stress_config=default_corporate_action_stresses()[3],
            events=(_event(adjustment_factor=0.6, expected_adjustment=0.5),),
        )
        self.assertEqual(scenario.finding_count, 1)
        self.assertIn("deviates from the expected", scenario.findings[0].message)
        self.assertFalse(scenario.not_qualified)
        self.assertIs(scenario.conclusion_severity, CoverageSeverity.WARNING)

    def test_price_adjustment_close_clean(self) -> None:
        scenario = _scenario(
            stress_config=default_corporate_action_stresses()[3],
            events=(_event(adjustment_factor=0.5, expected_adjustment=0.5),),
        )
        self.assertEqual(scenario.finding_count, 0)

    def test_findings_preserved(self) -> None:
        scenario = _scenario(
            events=(_event(terms=ActionTerms()), _event(action_id="ca-2", terms=ActionTerms()))
        )
        self.assertEqual(scenario.finding_count, 2)
        self.assertEqual([finding.action_id for finding in scenario.findings], ["ca-1", "ca-2"])

    def test_unsupported_type_skipped_for_missing_terms(self) -> None:
        # a RIGHTS_ISSUE (HK-only type) on a US market is not a missing-terms concern.
        scenario = _scenario(
            events=(_event(action_type=CorporateActionType.RIGHTS_ISSUE, terms=ActionTerms()),),
        )
        self.assertEqual(scenario.finding_count, 0)

    def test_report_context(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(scenario.code_version, "1.0.0")
        self.assertIs(scenario.market, Market.US)


class ReportTests(unittest.TestCase):
    """The stressed findings across the pre-registered range."""

    def test_report_has_four_scenarios(self) -> None:
        report = compute_corporate_action_stress_report(
            _oos_run(),
            market=Market.US,
            events=(_event(terms=ActionTerms()),),
        )
        self.assertEqual(len(report), 4)

    def test_scenario_lookup(self) -> None:
        report = compute_corporate_action_stress_report(
            _oos_run(),
            market=Market.US,
            events=(_event(terms=ActionTerms()),),
        )
        self.assertIsNotNone(report.scenario("ca-missing-terms"))
        self.assertIsNone(report.scenario("nope"))

    def test_key_unknown_makes_report_not_qualified(self) -> None:
        report = compute_corporate_action_stress_report(
            _oos_run(),
            market=Market.US,
            events=(_event(terms=ActionTerms()),),
        )
        self.assertTrue(report.not_qualified)
        self.assertIs(report.conclusion_severity, CoverageSeverity.NOT_QUALIFIED)

    def test_clean_events_clean_report(self) -> None:
        report = compute_corporate_action_stress_report(
            _oos_run(),
            market=Market.US,
            events=(_complete_event(),),
        )
        self.assertFalse(report.not_qualified)
        self.assertIsNone(report.conclusion_severity)

    def test_warning_only_not_qualified(self) -> None:
        report = compute_corporate_action_stress_report(
            _oos_run(),
            market=Market.US,
            events=(_event(adjustment_factor=0.6, expected_adjustment=0.5),),
        )
        # only the price-adjustment deviation triggers (a WARNING), not a key unknown.
        self.assertFalse(report.not_qualified)
        self.assertIs(report.conclusion_severity, CoverageSeverity.WARNING)

    def test_readable(self) -> None:
        report = compute_corporate_action_stress_report(
            _oos_run(),
            market=Market.US,
            events=(_event(terms=ActionTerms()),),
        )
        self.assertIn("4 corporate action stress scenario(s)", report.readable())


class FingerprintTests(unittest.TestCase):
    """Stable, re-derivable, sensitive corporate-action stress fingerprints."""

    def test_config_sha256_rederivable(self) -> None:
        stress = default_corporate_action_stresses()[0]
        self.assertEqual(len(stress.fingerprint), 64)
        int(stress.fingerprint, 16)
        self.assertEqual(stress.fingerprint, corporate_action_stress_config_fingerprint(stress))

    def test_config_changes_with_severity(self) -> None:
        base = default_corporate_action_stresses()[0]
        other = build_corporate_action_stress_config(
            version=base.version,
            source=base.source,
            kind=base.kind,
            severity=CoverageSeverity.ERROR,
        )
        self.assertNotEqual(base.fingerprint, other.fingerprint)

    def test_scenario_sha256_rederivable(self) -> None:
        scenario = _scenario()
        self.assertEqual(len(scenario.fingerprint), 64)
        int(scenario.fingerprint, 16)
        self.assertEqual(scenario.fingerprint, corporate_action_stress_fingerprint(scenario))

    def test_scenario_stable(self) -> None:
        self.assertEqual(_scenario().fingerprint, _scenario().fingerprint)

    def test_scenario_changes_with_events(self) -> None:
        self.assertNotEqual(
            _scenario().fingerprint,
            _scenario(events=(_complete_event(),)).fingerprint,
        )

    def test_scenario_changes_with_stress(self) -> None:
        self.assertNotEqual(
            _scenario().fingerprint,
            _scenario(stress_config=default_corporate_action_stresses()[3]).fingerprint,
        )

    def test_json_excludes_fingerprint_and_key_sorted(self) -> None:
        scenario = _scenario()
        serialized = corporate_action_stress_json(scenario)
        self.assertNotIn('"fingerprint"', serialized)
        self.assertIn('"version":"ca-missing-terms"', serialized)
        self.assertIn('"finding_count":1', serialized)
        self.assertIn('"not_qualified":true', serialized)
        payload = json.loads(serialized)
        self.assertEqual(
            serialized,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )

    def test_report_fingerprint_rederivable(self) -> None:
        report = compute_corporate_action_stress_report(
            _oos_run(),
            market=Market.US,
            events=(_event(terms=ActionTerms()),),
        )
        self.assertEqual(len(report.fingerprint), 64)
        self.assertEqual(report.fingerprint, corporate_action_stress_report_fingerprint(report))
