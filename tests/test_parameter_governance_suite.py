"""Parameter governance test suite (MVP 3 / SP 3.77).

Consolidated integration suite covering the seven acceptance dimensions —
参数空间 (parameter space, SP 3.15), 约束 (constraints, SP 3.16), 预算
(budget, SP 3.17), 确定性 (determinism, SP 3.18 / 3.28), 选择规则 (selection
rules, SP 3.21), 多重试验告警 (multiple-trial warning, SP 3.22) and 测试集隔离
(test-set isolation, SP 3.24) — and the interaction: the whole pipeline from
declared space → registered trials within budget → best selection → penalty
never touches the test set (parameter comparison is always denied). No
database is required.
"""

import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Market
from harbor.core.candidate_selection import (
    RiskConstraint,
    TrialValidationResult,
    rules_from_tuning,
    select_candidate,
)
from harbor.core.holdout_registry import register_test_set
from harbor.core.multiple_trial_penalty import (
    compute_trial_penalty,
    penalty_fingerprint,
)
from harbor.core.parameter_constraints import (
    ConstraintKind,
    MarketApplicabilityError,
    ParameterConstraintError,
    UnboundedSearchError,
    constraint,
    validate_parameter_set,
)
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    ParameterSpaceError,
    UndeclaredParameterError,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.test_access_guard import (
    AccessGuard,
    AccessGuardError,
    AccessKind,
)
from harbor.core.trial_budget import (
    BudgetExhaustedError,
    BudgetTracker,
    TieBreaker,
    TrialBudget,
    evaluate_early_stop,
    select_best_trial,
)
from harbor.core.trial_registry import build_trial_registry, trial_fingerprint
from harbor.core.validation_config import MetricDirection, TuningConfig
from harbor.core.validation_domain import Parameter, ParameterTrial, ValidationStatus

_FINGERPRINT = "dataset-fingerprint-demo"
_CODE_VERSION = "1.0.0"
_TRAIN_START = date(2019, 1, 1)
_TRAIN_END = date(2020, 12, 31)
_VALIDATION_START = date(2021, 1, 1)
_VALIDATION_END = date(2021, 12, 31)
_TEST_SET_ID = "holdout-1"
_CONFIG_HASH = "config-hash-demo"

_VALID = {"cash_weight": 0.05, "factor_weight": 0.95, "lookback": 252}

_WEIGHT_SUM = constraint(
    "weight-sum",
    ConstraintKind.SUM_TO_TARGET,
    "cash_weight",
    "factor_weight",
    target=1.0,
    reason="weights must sum to the equity target",
)
_WINDOW_EXCLUSIVE = constraint(
    "window-exclusive",
    ConstraintKind.EXCLUSIVE,
    "lookback",
    "window_days",
    reason="only one window type",
)
_FACTOR_IMPLIES_CASH = constraint(
    "factor-implies-cash",
    ConstraintKind.IMPLIES,
    "factor_weight",
    implied="cash_weight",
    reason="a factor weight requires a cash weight",
)


def _at(hour: int = 12) -> datetime:
    """Return a fixed UTC-aware timestamp (deterministic)."""
    return datetime(2026, 8, 9, hour, 0, 0, tzinfo=timezone.utc)


def _space():
    """Return the demo parameter space (SP 3.15)."""
    return build_parameter_space(
        declare_parameter(
            "cash_weight",
            ParameterKind.FACTOR_WEIGHT,
            domain=ParameterDomain.CONTINUOUS,
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            default=0.05,
        ),
        declare_parameter(
            "factor_weight",
            ParameterKind.FACTOR_WEIGHT,
            domain=ParameterDomain.CONTINUOUS,
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            default=0.95,
        ),
        declare_parameter(
            "lookback",
            ParameterKind.WINDOW,
            domain=ParameterDomain.INTEGER,
            minimum=60,
            maximum=504,
            step=24,
            default=252,
        ),
        declare_parameter(
            "window_days",
            ParameterKind.WINDOW,
            domain=ParameterDomain.INTEGER,
            minimum=60,
            maximum=504,
            step=24,
            default=252,
            markets=(Market.US,),
        ),
    )


def _budget(**overrides) -> TrialBudget:
    """Return a small deterministic trial budget (SP 3.17)."""
    fields = dict(max_trials=3, random_seed=42)
    fields.update(overrides)
    return TrialBudget(**fields)  # type: ignore[arg-type]


def _registry(**overrides):
    """Return a trial registry bound to the frozen context (SP 3.18)."""
    fields = dict(
        space=_space(),
        budget=_budget(),
        dataset_fingerprint=_FINGERPRINT,
        code_version=_CODE_VERSION,
        market=Market.HK,
        train_start=_TRAIN_START,
        train_end=_TRAIN_END,
        validation_start=_VALIDATION_START,
        validation_end=_VALIDATION_END,
        seed=42,
        constraints=(),
        trial_prefix="trial",
    )
    fields.update(overrides)
    return build_trial_registry(**fields)  # type: ignore[arg-type]


def _trial(metric: float, *, seed: int = 1, trial_id: str = "trial-1") -> ParameterTrial:
    """Return one recorded validation trial (SP 3.18)."""
    return ParameterTrial(
        trial_id=trial_id,
        parameters=tuple(Parameter(name=name, value=value) for name, value in _VALID.items()),
        dataset_fingerprint=_FINGERPRINT,
        train_start=_TRAIN_START,
        train_end=_TRAIN_END,
        validation_start=_VALIDATION_START,
        validation_end=_VALIDATION_END,
        seed=seed,
        code_version=_CODE_VERSION,
        metric=metric,
    )


def _independent_trials(*metrics: float) -> tuple[ParameterTrial, ...]:
    """Return trials with distinct seeds (independent fingerprints)."""
    return tuple(
        _trial(metric, seed=index + 1, trial_id=f"trial-{index + 1}")
        for index, metric in enumerate(metrics)
    )


def _results(*trial_ids: str, samples: int = 200) -> dict[str, TrialValidationResult]:
    """Return selection validation results (SP 3.21)."""
    return {
        trial_id: TrialValidationResult(
            trial_id=trial_id,
            metric_name="sharpe",
            validation_samples=samples,
        )
        for trial_id in trial_ids
    }


def _rules(**overrides):
    """Return pre-registered selection rules from a tuning config (SP 3.21)."""
    fields = dict(
        primary_metric="sharpe",
        metric_direction=MetricDirection.HIGHER_BETTER,
        max_trials=3,
        random_seed=42,
        min_validation_days=63,
    )
    fields.update(overrides)
    risk_constraints = fields.pop("risk_constraints", ())
    return rules_from_tuning(
        TuningConfig(**fields),  # type: ignore[arg-type]
        risk_constraints=risk_constraints,
    )


def _registration():
    """Register the independent holdout (SP 3.5)."""
    return register_test_set(
        test_set_id=_TEST_SET_ID,
        config_hash=_CONFIG_HASH,
        created_at=_at(0),
    )


class ParameterSpaceTests(unittest.TestCase):
    """SP 3.15 参数空间: declaration and value validation."""

    def setUp(self) -> None:
        self.space = _space()

    def test_space_declares_parameters(self) -> None:
        self.assertEqual(
            [parameter.name for parameter in self.space.parameters],
            ["cash_weight", "factor_weight", "lookback", "window_days"],
        )

    def test_validate_values_returns_ordered_parameters(self) -> None:
        parameters = self.space.validate_values(_VALID)
        self.assertEqual([parameter.name for parameter in parameters], list(_VALID))

    def test_undeclared_parameter_rejected(self) -> None:
        with self.assertRaises(UndeclaredParameterError):
            self.space.validate_values({"mystery": 0.5})
        with self.assertRaises(UndeclaredParameterError):
            self.space.require_declared("mystery")

    def test_out_of_range_value_rejected(self) -> None:
        with self.assertRaises(ParameterSpaceError):
            self.space.validate_values({"cash_weight": 1.5})

    def test_off_step_value_rejected(self) -> None:
        with self.assertRaises(ParameterSpaceError):
            self.space.validate_values({"cash_weight": 0.07})

    def test_integer_parameter_requires_int(self) -> None:
        with self.assertRaises(ParameterSpaceError):
            self.space.validate_values({"lookback": 252.5})


class ConstraintTests(unittest.TestCase):
    """SP 3.16 约束: combination and market/test-set gates."""

    def test_weight_sum_passes_with_whole_set(self) -> None:
        parameters = validate_parameter_set(
            _space(), _VALID, market=Market.HK, constraints=(_WEIGHT_SUM,)
        )
        self.assertEqual(len(parameters), 3)

    def test_weight_sum_fails_when_weight_missing(self) -> None:
        with self.assertRaises(ParameterConstraintError):
            validate_parameter_set(
                _space(),
                {"cash_weight": 0.05, "lookback": 252},
                market=Market.HK,
                constraints=(_WEIGHT_SUM,),
            )

    def test_exclusive_constraint_rejects_both(self) -> None:
        with self.assertRaises(ParameterConstraintError):
            validate_parameter_set(
                _space(),
                {"cash_weight": 0.05, "factor_weight": 0.95, "lookback": 252, "window_days": 252},
                market=Market.US,
                constraints=(_WINDOW_EXCLUSIVE,),
            )

    def test_implies_constraint_requires_implied(self) -> None:
        with self.assertRaises(ParameterConstraintError):
            validate_parameter_set(
                _space(),
                {"factor_weight": 0.95, "lookback": 252},
                market=Market.HK,
                constraints=(_FACTOR_IMPLIES_CASH,),
            )

    def test_market_applicability_rejected_on_wrong_market(self) -> None:
        with self.assertRaises(MarketApplicabilityError):
            validate_parameter_set(
                _space(),
                {"cash_weight": 0.05, "factor_weight": 0.95, "window_days": 252},
                market=Market.HK,
            )

    def test_unbounded_continuous_search_rejected(self) -> None:
        unbounded = build_parameter_space(
            declare_parameter(
                "free_weight",
                ParameterKind.FACTOR_WEIGHT,
                domain=ParameterDomain.CONTINUOUS,
                minimum=0.0,
                maximum=1.0,
                default=0.5,
            )
        )
        with self.assertRaises(UnboundedSearchError):
            validate_parameter_set(unbounded, {"free_weight": 0.5}, market=Market.HK)


class BudgetTests(unittest.TestCase):
    """SP 3.17 预算: a hard cap that never silently grows."""

    def test_tracker_allocates_until_exhausted(self) -> None:
        tracker = BudgetTracker(_budget(), used=0)
        self.assertEqual(tracker.remaining, 3)
        for _ in range(3):
            tracker = tracker.allocate()
        self.assertTrue(tracker.exhausted)
        self.assertEqual(tracker.remaining, 0)

    def test_allocate_beyond_budget_raises(self) -> None:
        tracker = BudgetTracker(_budget(), used=3)
        with self.assertRaises(BudgetExhaustedError):
            tracker.allocate()

    def test_registry_enforces_budget(self) -> None:
        registry = _registry()
        for metric in (0.10, 0.12, 0.08):
            registry, _ = registry.register(dict(_VALID), metric=metric)
        with self.assertRaises(BudgetExhaustedError):
            registry.register(dict(_VALID), metric=0.15)

    def test_target_metric_early_stop(self) -> None:
        budget = _budget(max_trials=100, early_stop="target_metric", early_stop_target=0.20)
        decision = evaluate_early_stop(
            budget, [0.10, 0.15, 0.21], direction=MetricDirection.HIGHER_BETTER
        )
        self.assertTrue(decision.should_stop)

    def test_no_improvement_early_stop_needs_window(self) -> None:
        budget = _budget(max_trials=100, early_stop="no_improvement", early_stop_trials=3)
        decision = evaluate_early_stop(
            budget, [0.12, 0.10, 0.11, 0.10], direction=MetricDirection.HIGHER_BETTER
        )
        self.assertTrue(decision.should_stop)
        self.assertIn("no improvement in the last 3 trials", decision.reason or "")


class DeterminismTests(unittest.TestCase):
    """SP 3.18 / 3.28 确定性: equal inputs replay to identical trials."""

    def _registered(self, *, seed: int):
        registry = _registry(seed=seed)
        trials = []
        for metric in (0.10, 0.12):
            registry, trial = registry.register(dict(_VALID), metric=metric)
            trials.append(trial)
        return trials

    def test_same_seed_replays_identical(self) -> None:
        first = self._registered(seed=42)
        second = self._registered(seed=42)
        self.assertEqual(first, second)
        self.assertEqual(
            [trial_fingerprint(trial) for trial in first],
            [trial_fingerprint(trial) for trial in second],
        )

    def test_different_seed_changes_fingerprint(self) -> None:
        self.assertNotEqual(
            trial_fingerprint(self._registered(seed=42)[0]),
            trial_fingerprint(self._registered(seed=7)[0]),
        )

    def test_fingerprint_excludes_trial_id_and_metric(self) -> None:
        registry = _registry()
        registry, first = registry.register(dict(_VALID), metric=0.10)
        registry, second = registry.register(dict(_VALID), metric=0.99)
        self.assertEqual(trial_fingerprint(first), trial_fingerprint(second))

    def test_fingerprint_changes_with_parameters(self) -> None:
        registry = _registry()
        registry, baseline = registry.register(dict(_VALID), metric=0.10)
        registry, changed = registry.register(
            {"cash_weight": 0.10, "factor_weight": 0.90, "lookback": 252}, metric=0.10
        )
        self.assertNotEqual(trial_fingerprint(baseline), trial_fingerprint(changed))


class SelectionRulesTests(unittest.TestCase):
    """SP 3.21 选择规则: pre-registered rules pick the best candidate."""

    def test_rules_from_tuning(self) -> None:
        rules = _rules()
        self.assertEqual(rules.primary_metric, "sharpe")
        self.assertEqual(rules.direction, MetricDirection.HIGHER_BETTER)
        self.assertEqual(rules.min_validation_samples, 63)

    def test_selects_best_by_metric(self) -> None:
        trials = _independent_trials(0.10, 0.12, 0.08)
        selection = select_candidate(trials, rules=_rules(), results=_results(*_trial_ids()))
        self.assertEqual(selection.selected.trial_id, "trial-2")
        self.assertEqual(selection.selected.metric, 0.12)

    def test_tie_breaker_first_keeps_earliest(self) -> None:
        trials = _independent_trials(0.12, 0.12)
        best = select_best_trial(
            trials, direction=MetricDirection.HIGHER_BETTER, tie_breaker=TieBreaker.FIRST
        )
        self.assertEqual(best.trial_id, "trial-1")

    def test_min_validation_samples_gate(self) -> None:
        trials = _independent_trials(0.12, 0.10)
        results = {
            "trial-1": TrialValidationResult("trial-1", "sharpe", validation_samples=50),
            "trial-2": TrialValidationResult("trial-2", "sharpe", validation_samples=200),
        }
        selection = select_candidate(trials, rules=_rules(), results=results)
        self.assertEqual(selection.selected.trial_id, "trial-2")
        self.assertIn("trial-1", [excluded.trial_id for excluded in selection.excluded])

    def test_risk_constraint_excludes_unmeasured(self) -> None:
        trials = _independent_trials(0.12, 0.10)
        rules = _rules(risk_constraints=(RiskConstraint("max_drawdown_pct", 30.0, "max dd"),))
        results = {
            "trial-1": TrialValidationResult(
                "trial-1", "sharpe", 200, risk={"max_drawdown_pct": 25.0}
            ),
            "trial-2": TrialValidationResult(
                "trial-2", "sharpe", 200, risk={"max_drawdown_pct": None}
            ),
        }
        selection = select_candidate(trials, rules=rules, results=results)
        self.assertEqual(selection.selected.trial_id, "trial-1")
        self.assertIn("trial-2", [excluded.trial_id for excluded in selection.excluded])

    def test_failed_trial_excluded_without_blocking(self) -> None:
        failed = replace(
            _trial(0.0, seed=1, trial_id="trial-failed"), metric=None, failed_reason="no data"
        )
        valid = _trial(0.12, seed=2, trial_id="trial-valid")
        selection = select_candidate(
            (failed, valid), rules=_rules(), results=_results("trial-valid")
        )
        self.assertEqual(selection.selected.trial_id, "trial-valid")
        self.assertIn("failed", selection.excluded[0].reason)


def _trial_ids() -> tuple[str, str, str]:
    """Return the three default independent trial ids."""
    return ("trial-1", "trial-2", "trial-3")


class MultipleTrialPenaltyTests(unittest.TestCase):
    """SP 3.22 多重试验告警: selection bias is surfaced and downgraded."""

    def test_single_trial_no_penalty(self) -> None:
        penalty = compute_trial_penalty(_independent_trials(0.12))
        self.assertEqual(penalty.trial_count, 1)
        self.assertEqual(penalty.downgrade, 0)
        self.assertIsNone(penalty.selection_bias_warning)

    def test_large_budget_downgrades(self) -> None:
        metrics = tuple(0.10 + 0.001 * index for index in range(25))
        penalty = compute_trial_penalty(_independent_trials(*metrics))
        self.assertEqual(penalty.trial_count, 25)
        self.assertGreaterEqual(penalty.downgrade, 1)
        self.assertIn("large trial budget", penalty.selection_bias_warning or "")

    def test_insignificant_gap_downgrades(self) -> None:
        penalty = compute_trial_penalty(_independent_trials(0.10, 0.1005))
        self.assertIn("significance", " ".join(penalty.reasons))

    def test_effective_df_counts_distinct_inputs(self) -> None:
        duplicate = tuple(
            _trial(metric, seed=1, trial_id=f"dup-{metric}") for metric in (0.10, 0.11, 0.12)
        )
        penalty = compute_trial_penalty(duplicate)
        self.assertEqual(penalty.effective_df, 1)
        self.assertEqual(penalty.duplicates, 2)

    def test_penalty_fingerprint_rederivable(self) -> None:
        penalty = compute_trial_penalty(_independent_trials(0.10, 0.12))
        self.assertEqual(penalty.fingerprint, penalty_fingerprint(penalty))


class TestSetIsolationTests(unittest.TestCase):
    """SP 3.24 测试集隔离: selection never reads the test set."""

    def setUp(self) -> None:
        self.guard = AccessGuard(registration=_registration())

    def test_parameter_comparison_always_denied(self) -> None:
        for stage in (
            ValidationStatus.TUNING,
            ValidationStatus.TEST_LOCKED,
            ValidationStatus.EVALUATED,
        ):
            with self.subTest(stage=stage.value):
                decision = self.guard.authorize(
                    AccessKind.PARAMETER_COMPARISON, current_stage=stage
                )[1]
                self.assertFalse(decision.granted)
                self.assertIn("selection is restricted", decision.reason or "")

    def test_data_read_denied_before_test_locked(self) -> None:
        decision = self.guard.authorize(
            AccessKind.DATA_READ, current_stage=ValidationStatus.TUNING
        )[1]
        self.assertFalse(decision.granted)

    def test_data_read_granted_at_test_locked(self) -> None:
        decision = self.guard.authorize(
            AccessKind.DATA_READ, current_stage=ValidationStatus.TEST_LOCKED
        )[1]
        self.assertTrue(decision.granted)

    def test_require_raises_on_denial_and_audits(self) -> None:
        with self.assertRaises(AccessGuardError):
            self.guard.require(
                AccessKind.PARAMETER_COMPARISON, current_stage=ValidationStatus.EVALUATED
            )
        self.assertEqual(
            len(self.guard.audit), 0
        )  # require returns a NEW guard; original unchanged

    def test_guard_is_immutable(self) -> None:
        updated, _ = self.guard.authorize(
            AccessKind.DATA_READ, current_stage=ValidationStatus.TEST_LOCKED
        )
        self.assertNotEqual(self.guard, updated)
        self.assertEqual(len(self.guard.audit), 0)
        self.assertEqual(len(updated.audit), 1)


class EndToEndGovernanceTests(unittest.TestCase):
    """The whole pipeline (space -> trials -> selection -> penalty) never reads the test set."""

    def test_full_pipeline_is_test_set_free(self) -> None:
        registry = _registry()
        for metric in (0.10, 0.12, 0.08):
            registry, _ = registry.register(dict(_VALID), metric=metric)
        rules = _rules()
        results = _results("trial-1", "trial-2", "trial-3")
        selection = select_candidate(registry.trials, rules=rules, results=results)
        self.assertEqual(selection.selected.trial_id, "trial-2")
        penalty = compute_trial_penalty(registry.trials)
        self.assertEqual(penalty.trial_count, 3)
        # The selection/penalty stage never reads the test set (always denied).
        decision = AccessGuard(registration=_registration()).authorize(
            AccessKind.PARAMETER_COMPARISON, current_stage=ValidationStatus.EVALUATED
        )[1]
        self.assertFalse(decision.granted)

    def test_selection_reads_only_validation_metric(self) -> None:
        # select_candidate only consumes trial.metric (validation) and the
        # validation result; there is no test-set metric in the interface.
        trials = _independent_trials(0.10, 0.12)
        selection = select_candidate(trials, rules=_rules(), results=_results("trial-1", "trial-2"))
        self.assertEqual(selection.selected.metric, 0.12)
        self.assertNotIn("test", selection.readable())

    def test_registry_replays_identically_across_runs(self) -> None:
        first = _registered_trials()
        second = _registered_trials()
        self.assertEqual(first, second)
        self.assertEqual(
            [trial_fingerprint(trial) for trial in first],
            [trial_fingerprint(trial) for trial in second],
        )


def _registered_trials():
    """Register two fixed trials through a fresh registry (SP 3.18)."""
    registry = _registry()
    trials = []
    for metric in (0.10, 0.12):
        registry, trial = registry.register(dict(_VALID), metric=metric)
        trials.append(trial)
    return tuple(trials)


if __name__ == "__main__":
    unittest.main()
