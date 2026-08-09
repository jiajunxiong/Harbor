"""Parameter budget tests (MVP 3 / SP 3.27).

Covers the parameter-search budget and validation flow end to end (deps
3.15–3.18): 预算耗尽 (the SP 3.17/3.18 budget is a hard cap — a trial beyond
it raises and is never silently appended), 重复试验 (equal inputs replay to
identical trial fingerprints — SP 3.28 — while each registration is recorded),
未声明参数 (SP 3.15/3.16 rejects undeclared parameters), 无效参数组合 (range,
step, type, combination-constraint, market and unbounded-search violations all
rejected), and 确定性随机种子 (the same seed produces identical trials and
fingerprints; the seed is part of the trial identity).
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.parameter_constraints import (
    ConstraintKind,
    MarketApplicabilityError,
    ParameterConstraintError,
    UnboundedSearchError,
    constraint,
)
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    ParameterSpace,
    ParameterSpaceError,
    UndeclaredParameterError,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.trial_budget import BudgetExhaustedError, BudgetTracker, TrialBudget
from harbor.core.trial_registry import (
    TrialRegistry,
    build_trial_registry,
    trial_fingerprint,
)

_TRAIN_START = date(2019, 1, 1)
_TRAIN_END = date(2020, 12, 31)
_VALIDATION_START = date(2021, 1, 1)
_VALIDATION_END = date(2022, 12, 31)

_VALID = {"cash_weight": 0.05, "factor_weight": 0.95, "lookback": 252}
_WEIGHT_SUM = constraint(
    "weights_sum_to_one",
    ConstraintKind.SUM_TO_TARGET,
    "cash_weight",
    "factor_weight",
    target=1.0,
    reason="portfolio weights must sum to 1",
)


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


def _budget(**overrides: object) -> TrialBudget:
    """Return a small trial budget with overridable fields."""
    fields: dict[str, object] = {"max_trials": 3, "random_seed": 42}
    fields.update(overrides)
    return TrialBudget(**fields)  # type: ignore[arg-type]


def _registry(**overrides: object) -> TrialRegistry:
    """Return a trial registry over the frozen context (SP 3.18)."""
    kwargs: dict[str, object] = {
        "space": _space(),
        "budget": _budget(),
        "dataset_fingerprint": "f" * 64,
        "code_version": "1.0.0",
        "market": Market.HK,
        "train_start": _TRAIN_START,
        "train_end": _TRAIN_END,
        "validation_start": _VALIDATION_START,
        "validation_end": _VALIDATION_END,
        "seed": 42,
        "constraints": (),
        "trial_prefix": "trial",
    }
    kwargs.update(overrides)
    return build_trial_registry(**kwargs)  # type: ignore[arg-type]


class BudgetExhaustionTests(unittest.TestCase):
    """Verifies the budget is a hard cap (预算耗尽, SP 3.17/3.18)."""

    def test_budget_exhausted_after_max_trials(self) -> None:
        registry = _registry()
        for _ in range(3):
            registry, _ = registry.register(dict(_VALID), metric=0.12)
        with self.assertRaises(BudgetExhaustedError):
            registry.register(dict(_VALID), metric=0.12)

    def test_remaining_and_exhausted_properties(self) -> None:
        registry = _registry()
        self.assertEqual(registry.remaining, 3)
        self.assertFalse(registry.exhausted)
        for _ in range(3):
            registry, _ = registry.register(dict(_VALID), metric=0.12)
        self.assertEqual(registry.remaining, 0)
        self.assertTrue(registry.exhausted)

    def test_failed_registration_does_not_append(self) -> None:
        registry = _registry()
        for _ in range(3):
            registry, _ = registry.register(dict(_VALID), metric=0.12)
        before = registry.trials
        with self.assertRaises(BudgetExhaustedError):
            registry.register(dict(_VALID), metric=0.12)
        self.assertEqual(registry.trials, before)

    def test_budget_is_hard_cap_even_for_new_params(self) -> None:
        registry = _registry()
        for _ in range(3):
            registry, _ = registry.register(dict(_VALID), metric=0.12)
        different = {"cash_weight": 0.10, "factor_weight": 0.90, "lookback": 300}
        with self.assertRaises(BudgetExhaustedError):
            registry.register(different, metric=0.12)

    def test_budget_tracker_allocate_raises_beyond_cap(self) -> None:
        tracker = BudgetTracker(_budget(), used=0)
        for _ in range(3):
            tracker = tracker.allocate()
        with self.assertRaises(BudgetExhaustedError):
            tracker.allocate()


class DuplicateTrialTests(unittest.TestCase):
    """Verifies duplicate trials are recorded and fingerprint identically (重复试验)."""

    def test_same_inputs_same_fingerprint_different_trial_id(self) -> None:
        registry = _registry()
        registry, trial_a = registry.register(dict(_VALID), metric=0.12)
        registry, trial_b = registry.register(dict(_VALID), metric=0.12)
        self.assertEqual(trial_fingerprint(trial_a), trial_fingerprint(trial_b))
        self.assertEqual(trial_a.trial_id, "trial-1")
        self.assertEqual(trial_b.trial_id, "trial-2")

    def test_duplicates_both_recorded(self) -> None:
        registry = _registry()
        registry, _ = registry.register(dict(_VALID), metric=0.12)
        registry, _ = registry.register(dict(_VALID), metric=0.12)
        self.assertEqual(len(registry.trials), 2)
        self.assertEqual(registry.used, 2)

    def test_duplicate_inputs_replay_identically_across_registries(self) -> None:
        registry_a, trial_a = _registry().register(dict(_VALID), metric=0.12)
        registry_b, trial_b = _registry().register(dict(_VALID), metric=0.12)
        self.assertEqual(trial_fingerprint(trial_a), trial_fingerprint(trial_b))
        self.assertEqual(trial_a, trial_b)


class UndeclaredParameterTests(unittest.TestCase):
    """Verifies undeclared parameters are rejected (未声明参数, SP 3.15/3.16)."""

    def test_undeclared_parameter_rejected(self) -> None:
        registry = _registry()
        with self.assertRaises(UndeclaredParameterError):
            registry.register({"cash_weight": 0.05, "not_declared": 0.5})

    def test_error_mentions_parameter_name(self) -> None:
        with self.assertRaises(UndeclaredParameterError) as ctx:
            _registry().register({"phantom": 1.0})
        self.assertIn("phantom", str(ctx.exception))

    def test_registry_unchanged_after_failure(self) -> None:
        registry = _registry()
        with self.assertRaises(UndeclaredParameterError):
            registry.register({"cash_weight": 0.05, "phantom": 1.0})
        self.assertEqual(registry.trials, ())

    def test_require_declared_direct(self) -> None:
        space = _space()
        space.require_declared("cash_weight")
        with self.assertRaises(UndeclaredParameterError):
            space.require_declared("phantom")


class InvalidCombinationTests(unittest.TestCase):
    """Verifies invalid parameter combinations are rejected (无效参数组合)."""

    def test_out_of_range_rejected(self) -> None:
        with self.assertRaises(ParameterSpaceError):
            _registry().register({"cash_weight": 2.0})

    def test_off_step_rejected(self) -> None:
        with self.assertRaises(ParameterSpaceError):
            _registry().register({"cash_weight": 0.07})

    def test_non_numeric_rejected(self) -> None:
        with self.assertRaises(ParameterSpaceError):
            _registry().register({"cash_weight": "high"})

    def test_sum_constraint_violation_rejected(self) -> None:
        registry = _registry(constraints=(_WEIGHT_SUM,))
        with self.assertRaises(ParameterConstraintError):
            registry.register({"cash_weight": 0.05})

    def test_sum_constraint_passes_with_whole_weight_set(self) -> None:
        registry = _registry(constraints=(_WEIGHT_SUM,))
        registry, trial = registry.register(dict(_VALID), metric=0.12)
        self.assertEqual(trial.parameter("cash_weight"), 0.05)
        self.assertEqual(trial.parameter("factor_weight"), 0.95)

    def test_market_mismatch_rejected(self) -> None:
        hk_only = build_parameter_space(
            declare_parameter(
                name="hk_only",
                kind=ParameterKind.FACTOR_WEIGHT,
                domain=ParameterDomain.CONTINUOUS,
                minimum=0.0,
                maximum=1.0,
                step=0.1,
                default=0.1,
                markets=(Market.HK,),
            )
        )
        registry = _registry(space=hk_only, market=Market.US, constraints=())
        with self.assertRaises(MarketApplicabilityError):
            registry.register({"hk_only": 0.1})

    def test_unbounded_continuous_search_rejected(self) -> None:
        unbounded = build_parameter_space(
            declare_parameter(
                name="free",
                kind=ParameterKind.FACTOR_WEIGHT,
                domain=ParameterDomain.CONTINUOUS,
                minimum=0.0,
                maximum=1.0,
                default=0.1,
                markets=(Market.HK,),
            )
        )
        registry = _registry(space=unbounded, constraints=())
        with self.assertRaises(UnboundedSearchError):
            registry.register({"free": 0.1})


class DeterministicSeedTests(unittest.TestCase):
    """Verifies deterministic behavior under the same seed (确定性随机种子)."""

    def test_same_seed_produces_identical_trials(self) -> None:
        registry_a, trial_a = _registry().register(dict(_VALID), metric=0.12)
        registry_b, trial_b = _registry().register(dict(_VALID), metric=0.12)
        self.assertEqual(trial_a, trial_b)
        self.assertEqual(trial_fingerprint(trial_a), trial_fingerprint(trial_b))
        self.assertEqual(trial_a.seed, 42)

    def test_different_seed_changes_fingerprint(self) -> None:
        _, trial_a = _registry(seed=1).register(dict(_VALID), metric=0.12)
        _, trial_b = _registry(seed=2).register(dict(_VALID), metric=0.12)
        self.assertNotEqual(trial_fingerprint(trial_a), trial_fingerprint(trial_b))

    def test_seed_is_part_of_trial_identity(self) -> None:
        _, trial = _registry().register(dict(_VALID), metric=0.12)
        self.assertEqual(trial.seed, 42)
        self.assertEqual(trial.code_version, "1.0.0")
        self.assertEqual(trial.dataset_fingerprint, "f" * 64)

    def test_budget_random_seed_is_deterministic(self) -> None:
        budget_a = _budget()
        budget_b = _budget()
        self.assertEqual(budget_a.random_seed, budget_b.random_seed)
        self.assertEqual(budget_a.random_seed, 42)
        # Equal budgets track identically.
        tracker_a = BudgetTracker(budget_a, used=0)
        tracker_b = BudgetTracker(budget_b, used=0)
        self.assertEqual(tracker_a.allocate(), tracker_b.allocate())


if __name__ == "__main__":
    unittest.main()
