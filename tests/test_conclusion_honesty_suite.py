"""Conclusion honesty test suite (MVP 3 / SP 3.81).

Verifies that the five honesty threats — 泄漏 (leakage), 关键数据缺口 (critical
data gaps), 失败折叠 (failed folds), 测试后调整 (post-test adjustment) and
样本不足 (insufficient samples) — can never output ``QUALIFIED`` (确认不得输出
QUALIFIED). Two conclusion layers are exercised together:

- SP 3.58 ``adjudicate_stability`` turns every failure signal into
  ``NOT_QUALIFIED`` (a FAIL dominates) and every missing-evidence signal into
  ``INCONCLUSIVE`` (never a pass);
- SP 3.64 ``OosStructuredConclusion.overall`` aggregates the stability
  conclusion + unresolved limitations — only a qualified stability with no
  unresolved limitation is ``QUALIFIED``;
- the structural guards make the threats impossible or refuse reuse:
  leakage attempts are always denied + audited (SP 3.24/3.5), post-test
  changes require a new test-set version + new run (SP 3.42) and a drifted
  fingerprint refuses reuse (SP 3.11).

No database is required.
"""

import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Currency, Market
from harbor.core.candidate_selection import (
    TrialValidationResult,
    rules_from_tuning,
    select_candidate,
)
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.data_drift import DataDriftError, require_fingerprint_matches
from harbor.core.dataset_fingerprint import dataset_fingerprint
from harbor.core.dataset_manifest import build_dataset_manifest
from harbor.core.final_holdout import FinalHoldoutInputs, unlock_final_holdout
from harbor.core.holdout_registry import (
    HoldoutAccessError,
    guard_parameter_selection,
    register_test_set,
)
from harbor.core.oos_conclusion import (
    OosStructuredConclusion,
    build_oos_conclusion,
    no_return_promise_statement,
)
from harbor.core.performance_metrics import PerformanceMetrics
from harbor.core.reaccess_policy import (
    ReaccessPolicyError,
    require_test_reaccess_compliance,
)
from harbor.core.stability_rule import (
    StabilityConclusion,
    StabilitySignals,
    adjudicate_stability,
    default_stability_rule,
)
from harbor.core.test_access_guard import AccessGuard, AccessKind
from harbor.core.trial_budget import TrialBudget
from harbor.core.validation_config import MetricDirection, TuningConfig
from harbor.core.validation_domain import (
    ManifestComponent,
    OOSConclusion,
    Parameter,
    ParameterTrial,
    ValidationStatus,
)

_FINGERPRINT = "dataset-fingerprint-demo"
_CODE_VERSION = "1.0.0"
_TRAIN_START = date(2019, 1, 1)
_TRAIN_END = date(2020, 12, 31)
_VALIDATION_START = date(2021, 1, 1)
_VALIDATION_END = date(2021, 12, 31)
_TEST_SET_ID = "holdout-1"
_CONFIG_HASH = "config-hash-demo"


def _at(hour: int = 12) -> datetime:
    return datetime(2026, 8, 9, hour, 0, 0, tzinfo=timezone.utc)


def _signals(**overrides) -> StabilitySignals:
    """Return clean stability signals (all dimensions PASS)."""
    fields = dict(
        market=Market.HK,
        dataset_fingerprint=_FINGERPRINT,
        code_version=_CODE_VERSION,
        fold_spread=0.05,
        fold_count=4,
        fold_failure_count=0,
        neighborhood_cliff_ratio=0.1,
        neighborhood_infeasible_ratio=0.0,
        environment_insufficient_ratio=0.0,
        max_stress_loss_pct=2.0,
        stress_unquantifiable=False,
        coverage_blocked=False,
    )
    fields.update(overrides)
    return StabilitySignals(**fields)  # type: ignore[arg-type]


def _stability(**overrides) -> StabilityConclusion:
    """Adjudicate the SP 3.58 stability conclusion over the signals."""
    return adjudicate_stability(_signals(**overrides), config=default_stability_rule())


def _performance(**overrides) -> PerformanceMetrics:
    fields = dict(
        start_date=_TRAIN_START,
        end_date=_VALIDATION_END,
        periods=252,
        cumulative_return=0.05,
        annualized_return=0.20,
        annualized_volatility=0.15,
        max_drawdown=-0.05,
        sharpe_ratio=1.2,
        calmar_ratio=1.0,
        downside_deviation=0.08,
    )
    fields.update(overrides)
    return PerformanceMetrics(**fields)  # type: ignore[arg-type]


def _coverage(**overrides) -> MarketCoverage:
    fields = dict(
        market=Market.HK,
        scores=(
            CoverageScore(
                market=Market.HK,
                item=ManifestComponent.PRICES,
                measurement=CoverageMeasurement(covered=100, denominator=100),
            ),
        ),
    )
    fields.update(overrides)
    return MarketCoverage(**fields)  # type: ignore[arg-type]


def _budget(**overrides) -> TrialBudget:
    fields = dict(max_trials=20, random_seed=42)
    fields.update(overrides)
    return TrialBudget(**fields)  # type: ignore[arg-type]


def _conclusion(**overrides) -> OosStructuredConclusion:
    """Build a structured OOS conclusion (SP 3.64) with overridable inputs."""
    fields = dict(
        version="conclusion-1.0",
        source="pre-registered",
        market=Market.HK,
        dataset_fingerprint=_FINGERPRINT,
        code_version=_CODE_VERSION,
        performance=_performance(),
        benchmark_return=0.02,
        excess_return=0.03,
        coverage=_coverage(),
        stability=_stability(),
        budget=_budget(),
        unresolved_limitations=(),
    )
    fields.update(overrides)
    return build_oos_conclusion(**fields)  # type: ignore[arg-type]


def _registration():
    return register_test_set(
        test_set_id=_TEST_SET_ID,
        config_hash=_CONFIG_HASH,
        created_at=_at(0),
    )


def _guard() -> AccessGuard:
    return AccessGuard(registration=_registration())


def _inputs(**overrides) -> FinalHoldoutInputs:
    fields = dict(
        test_set_id=_TEST_SET_ID,
        dataset_fingerprint=_FINGERPRINT,
        config_hash=_CONFIG_HASH,
        selection_fingerprint="selection-fp",
        code_version=_CODE_VERSION,
    )
    fields.update(overrides)
    return FinalHoldoutInputs(**fields)  # type: ignore[arg-type]


def _release():
    return unlock_final_holdout(
        _registration(),
        current_stage=ValidationStatus.EVALUATED,
        responsibility="researcher",
        inputs=_inputs(),
        unlocked_at=_at(1),
    )


def _manifest(**overrides):
    fields = dict(
        markets=(Market.HK,),
        base_currency=Currency.HKD,
        start_date=_TRAIN_START,
        end_date=_VALIDATION_END,
        data_cutoff=_VALIDATION_END,
        config_hash=_CONFIG_HASH,
        code_version=_CODE_VERSION,
        calendar_version="cal-1",
        fx_source="mock",
        fingerprint="unfingerprinted",
    )
    fields.update(overrides)
    manifest = build_dataset_manifest(**fields)  # type: ignore[arg-type]
    return replace(manifest, fingerprint=dataset_fingerprint(manifest))


class LeakageHonestyTests(unittest.TestCase):
    """SP 3.24/3.5 泄漏: a test-set leak is always denied and audited."""

    def setUp(self) -> None:
        self.guard = _guard()

    def test_parameter_comparison_denied_at_every_stage(self) -> None:
        for stage in ValidationStatus:
            with self.subTest(stage=stage.value):
                decision = self.guard.authorize(
                    AccessKind.PARAMETER_COMPARISON, current_stage=stage
                )[1]
                self.assertFalse(decision.granted)
                self.assertIn("selection is restricted", decision.reason or "")

    def test_data_read_denied_before_test_locked(self) -> None:
        for stage in (
            ValidationStatus.DRAFT,
            ValidationStatus.DATA_FROZEN,
            ValidationStatus.TUNING,
        ):
            with self.subTest(stage=stage.value):
                decision = self.guard.authorize(AccessKind.DATA_READ, current_stage=stage)[1]
                self.assertFalse(decision.granted)
        self.assertEqual(len(self.guard.audit), 0)  # original guard unchanged (immutable)

    def test_leak_attempt_is_audited(self) -> None:
        updated = self.guard.authorize(
            AccessKind.PARAMETER_COMPARISON, current_stage=ValidationStatus.TEST_LOCKED
        )[0]
        self.assertEqual(len(updated.audit), 1)
        entry = updated.audit[0]
        self.assertEqual(entry.access_kind, AccessKind.PARAMETER_COMPARISON)
        self.assertFalse(entry.granted)

    def test_parameter_selection_guard_always_raises(self) -> None:
        with self.assertRaises(HoldoutAccessError):
            guard_parameter_selection(_registration(), ValidationStatus.EVALUATED)


class CriticalDataGapHonestyTests(unittest.TestCase):
    """SP 3.10/3.58 关键数据缺口: a critical gap blocks QUALIFIED."""

    def test_coverage_blocked_is_not_qualified(self) -> None:
        self.assertEqual(_stability(coverage_blocked=True).conclusion, OOSConclusion.NOT_QUALIFIED)

    def test_gapped_coverage_overall_not_qualified(self) -> None:
        stability = _stability(coverage_blocked=True)
        conclusion = _conclusion(stability=stability)
        self.assertEqual(conclusion.overall, OOSConclusion.NOT_QUALIFIED)

    def test_gapped_coverage_score_drives_the_gate(self) -> None:
        coverage = _coverage(
            scores=(
                CoverageScore(
                    market=Market.HK,
                    item=ManifestComponent.PRICES,
                    measurement=CoverageMeasurement(covered=50, denominator=100),
                ),
            )
        )
        self.assertLess(coverage.overall_pct, 95.0)
        stability = _stability(coverage_blocked=True)
        self.assertNotEqual(
            _conclusion(coverage=coverage, stability=stability).overall,
            OOSConclusion.QUALIFIED,
        )

    def test_unresolved_gap_limitation_is_not_qualified(self) -> None:
        conclusion = _conclusion(unresolved_limitations=("missing FX data on US leg",))
        self.assertEqual(conclusion.overall, OOSConclusion.INCONCLUSIVE)
        self.assertNotEqual(conclusion.overall, OOSConclusion.QUALIFIED)


class FailedFoldHonestyTests(unittest.TestCase):
    """SP 3.43/3.58 失败折叠: a failed fold never yields QUALIFIED."""

    def test_any_failed_fold_is_not_qualified(self) -> None:
        self.assertEqual(
            _stability(fold_count=4, fold_failure_count=1).conclusion,
            OOSConclusion.NOT_QUALIFIED,
        )

    def test_no_executed_fold_is_inconclusive(self) -> None:
        self.assertEqual(_stability(fold_count=0).conclusion, OOSConclusion.INCONCLUSIVE)

    def test_failed_fold_overall_not_qualified(self) -> None:
        stability = _stability(fold_count=4, fold_failure_count=2)
        conclusion = _conclusion(stability=stability)
        self.assertEqual(conclusion.overall, OOSConclusion.NOT_QUALIFIED)


class PostTestAdjustmentHonestyTests(unittest.TestCase):
    """SP 3.42/3.11 测试后调整: a post-test change never reuses a qualified conclusion."""

    def setUp(self) -> None:
        self.release = _release()

    def test_unchanged_inputs_may_reuse(self) -> None:
        decision = require_test_reaccess_compliance(self.release, _inputs())
        self.assertEqual(decision.changes, ())

    def test_changed_inputs_require_new_test_set_and_run(self) -> None:
        with self.assertRaises(ReaccessPolicyError) as context:
            require_test_reaccess_compliance(
                self.release, _inputs(selection_fingerprint="selection-fp-v2")
            )
        self.assertIn("new test-set version", str(context.exception))

    def test_data_drift_refuses_old_conclusion(self) -> None:
        current = _manifest()
        changed = _manifest(calendar_version="cal-2")
        with self.assertRaises(DataDriftError):
            require_fingerprint_matches(current, dataset_fingerprint(changed))

    def test_matching_fingerprint_allows_reuse(self) -> None:
        require_fingerprint_matches(_manifest(), dataset_fingerprint(_manifest()))


class InsufficientSampleHonestyTests(unittest.TestCase):
    """SP 3.50/3.21 样本不足: insufficient samples never yield QUALIFIED."""

    def test_insufficient_environment_ratio_not_qualified(self) -> None:
        self.assertEqual(
            _stability(environment_insufficient_ratio=0.75).conclusion,
            OOSConclusion.NOT_QUALIFIED,
        )

    def test_missing_environment_evidence_inconclusive(self) -> None:
        self.assertEqual(
            _stability(environment_insufficient_ratio=None).conclusion,
            OOSConclusion.INCONCLUSIVE,
        )

    def test_below_min_validation_samples_excludes_candidate(self) -> None:
        rules = rules_from_tuning(
            TuningConfig(
                primary_metric="sharpe",
                metric_direction=MetricDirection.HIGHER_BETTER,
                min_validation_days=63,
            )
        )
        trial = ParameterTrial(
            trial_id="trial-1",
            parameters=(Parameter(name="cash_weight", value=0.05),),
            dataset_fingerprint=_FINGERPRINT,
            train_start=_TRAIN_START,
            train_end=_TRAIN_END,
            validation_start=_VALIDATION_START,
            validation_end=_VALIDATION_END,
            seed=42,
            code_version=_CODE_VERSION,
            metric=0.12,
        )
        selection = select_candidate(
            (trial,),
            rules=rules,
            results={"trial-1": TrialValidationResult("trial-1", "sharpe", validation_samples=50)},
        )
        self.assertIsNone(selection.selected)
        self.assertEqual(len(selection.excluded), 1)


class StructuredConclusionHonestyTests(unittest.TestCase):
    """SP 3.64 结论聚合: only clean stability + no limitation is QUALIFIED."""

    def test_clean_conclusion_is_qualified(self) -> None:
        self.assertEqual(_conclusion().overall, OOSConclusion.QUALIFIED)

    def test_not_qualified_stability_dominates(self) -> None:
        stability = _stability(max_stress_loss_pct=15.0)
        self.assertEqual(_conclusion(stability=stability).overall, OOSConclusion.NOT_QUALIFIED)

    def test_inconclusive_stability_is_inconclusive(self) -> None:
        stability = _stability(fold_spread=None)
        self.assertEqual(_conclusion(stability=stability).overall, OOSConclusion.INCONCLUSIVE)

    def test_any_limitation_degrades_qualified(self) -> None:
        conclusion = _conclusion(unresolved_limitations=("limited OOS horizon",))
        self.assertEqual(conclusion.overall, OOSConclusion.INCONCLUSIVE)
        self.assertNotEqual(conclusion.overall, OOSConclusion.QUALIFIED)

    def test_no_return_promise_statement(self) -> None:
        statement = no_return_promise_statement()
        self.assertIn("no projection, guarantee or promise of future returns", statement)
        self.assertIn("结论不含收益承诺", statement)
        self.assertIn("no return promise", _conclusion().readable())


if __name__ == "__main__":
    unittest.main()
